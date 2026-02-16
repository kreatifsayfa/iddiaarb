<?php
declare(strict_types=1);

header('Content-Type: text/event-stream; charset=utf-8');
header('Cache-Control: no-cache, no-transform');
header('Connection: keep-alive');

$config = require __DIR__ . '/config.php';

function fetch_json_via_http(string $url, int $timeoutSec): array
{
    $isJsonPayload = static function (string $raw): bool {
        $trimmed = trim($raw);
        if ($trimmed === '') {
            return false;
        }
        json_decode($trimmed, true);
        return json_last_error() === JSON_ERROR_NONE;
    };

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        if ($ch !== false) {
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_FOLLOWLOCATION => true,
                CURLOPT_CONNECTTIMEOUT => max(3, min(15, $timeoutSec)),
                CURLOPT_TIMEOUT => $timeoutSec,
                CURLOPT_HTTPHEADER => ['Accept: application/json', 'User-Agent: iddiaarb-stream/1.0'],
            ]);
            $body = curl_exec($ch);
            $err = curl_error($ch);
            $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
            curl_close($ch);
            if (is_string($body) && $isJsonPayload($body)) {
                return ['ok' => true, 'body' => $body, 'error' => ''];
            }
            return [
                'ok' => false,
                'body' => '',
                'error' => 'curl_error=' . ($err !== '' ? $err : ('http_' . $code)),
            ];
        }
    }

    $ctx = stream_context_create([
        'http' => [
            'method' => 'GET',
            'timeout' => $timeoutSec,
            'ignore_errors' => true,
            'header' => "Accept: application/json\r\nUser-Agent: iddiaarb-stream/1.0\r\n",
        ],
    ]);
    $body = @file_get_contents($url, false, $ctx);
    if (is_string($body) && $isJsonPayload($body)) {
        return ['ok' => true, 'body' => $body, 'error' => ''];
    }
    $last = error_get_last();
    $msg = is_array($last) && isset($last['message']) ? (string) $last['message'] : 'http_fetch_failed';
    return ['ok' => false, 'body' => '', 'error' => $msg];
}

function fetch_json_via_php_cli(array $query): array
{
    $candidates = [];
    $envBin = getenv('PHP_CLI_BIN') ?: '';
    if ($envBin !== '') {
        $candidates[] = $envBin;
    }
    $cfgBin = isset($GLOBALS['config']['php_cli_bin']) && is_string($GLOBALS['config']['php_cli_bin'])
        ? trim((string)$GLOBALS['config']['php_cli_bin'])
        : '';
    if ($cfgBin !== '') {
        $candidates[] = $cfgBin;
    }
    $candidates[] = dirname(PHP_BINARY) . DIRECTORY_SEPARATOR . 'php.exe';
    $candidates[] = 'C:\\xampp\\php\\php.exe';
    $candidates[] = 'php';

    $phpBin = '';
    foreach ($candidates as $candidate) {
        if ($candidate === 'php') {
            $phpBin = 'php';
            break;
        }
        if (is_file($candidate)) {
            $phpBin = $candidate;
            break;
        }
    }
    if ($phpBin === '') {
        $phpBin = 'php';
    }

    $queryString = http_build_query($query);
    $arg = base64_encode($queryString);
    $apiPath = str_replace('\\', '/', __DIR__ . '/api.php');
    $inline = 'parse_str(base64_decode($argv[1] ?? ""), $_GET); include ' . var_export($apiPath, true) . ';';
    $cmd = escapeshellcmd($phpBin) . ' -r ' . escapeshellarg($inline) . ' ' . escapeshellarg($arg);

    $desc = [
        0 => ['pipe', 'r'],
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ];
    $proc = @proc_open($cmd, $desc, $pipes, realpath(__DIR__ . '/..') ?: __DIR__);
    if (!is_resource($proc)) {
        return ['ok' => false, 'body' => '', 'error' => 'php_cli_proc_open_failed'];
    }
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exit = proc_close($proc);

    if ($exit !== 0 || !is_string($stdout) || trim($stdout) === '') {
        $err = trim((string) $stderr);
        if ($err === '') {
            $err = 'php_cli_exit_' . $exit;
        }
        return ['ok' => false, 'body' => '', 'error' => $err];
    }
    return ['ok' => true, 'body' => $stdout, 'error' => ''];
}

$interval = isset($_GET['interval']) && is_numeric($_GET['interval']) ? (int)$_GET['interval'] : 20;
if ($interval < 3) {
    $interval = 3;
}
if ($interval > 120) {
    $interval = 120;
}

$query = $_GET;
unset($query['interval']);
$query['refresh'] = '1';

$scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
$host = $_SERVER['HTTP_HOST'] ?? '127.0.0.1';
$basePath = rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/')), '/');
$apiUrl = $scheme . '://' . $host . $basePath . '/api.php?' . http_build_query($query);
$httpTimeout = (int) max(45, min(180, $interval * 6));

echo ": stream_connected " . gmdate('c') . "\n\n";
@ob_flush();
@flush();

for ($i = 0; $i < 500; $i++) {
    if (connection_aborted()) {
        break;
    }

    echo ": stream_tick " . gmdate('c') . "\n\n";
    @ob_flush();
    @flush();

    $httpResult = fetch_json_via_http($apiUrl, $httpTimeout);
    if ($httpResult['ok'] === true) {
        $json = (string) $httpResult['body'];
        echo 'data: ' . $json . "\n\n";
    } else {
        $cliResult = fetch_json_via_php_cli($query);
        if ($cliResult['ok'] === true) {
            $json = (string) $cliResult['body'];
            echo 'data: ' . $json . "\n\n";
        } else {
            $payload = [
                'ok' => false,
                'error' => 'stream_fetch_failed',
                'http_error' => (string) $httpResult['error'],
                'cli_error' => (string) $cliResult['error'],
                'api_url' => $apiUrl,
                'generated_at' => gmdate('c'),
            ];
            echo 'data: ' . json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n\n";
        }
    }

    if ((int)($config['stream_heartbeat_every'] ?? 1) > 0 && ($i % (int)($config['stream_heartbeat_every'] ?? 1) === 0)) {
        echo ": heartbeat " . gmdate('c') . "\n\n";
    }

    @ob_flush();
    @flush();
    sleep($interval);
}
