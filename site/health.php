<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

$config = require __DIR__ . '/config.php';
$projectRoot = realpath(__DIR__ . '/..');
$pythonBin = (string)($config['python_bin'] ?? 'python');

$payload = [
    'ok' => true,
    'timestamp' => gmdate('c'),
    'php_version' => PHP_VERSION,
];

$cmd = escapeshellcmd($pythonBin) . ' -m iddiaarb.cli health-check --format json';
$desc = [
    0 => ['pipe', 'r'],
    1 => ['pipe', 'w'],
    2 => ['pipe', 'w'],
];
$proc = @proc_open($cmd, $desc, $pipes, $projectRoot ?: __DIR__);
if (!is_resource($proc)) {
    $payload['ok'] = false;
    $payload['python'] = 'unavailable';
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
fclose($pipes[0]);
$stdout = stream_get_contents($pipes[1]);
$stderr = stream_get_contents($pipes[2]);
fclose($pipes[1]);
fclose($pipes[2]);
$code = proc_close($proc);

if ($code !== 0) {
    $payload['ok'] = false;
    $payload['python'] = 'error';
    $payload['stderr'] = trim((string)$stderr);
} else {
    $decoded = json_decode((string)$stdout, true);
    if (is_array($decoded)) {
        $payload['python'] = $decoded;
    } else {
        $payload['python'] = 'invalid_json';
        $payload['ok'] = false;
    }
}

echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
