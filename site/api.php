<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

$config = require __DIR__ . '/config.php';

function respond(int $code, array $payload): void
{
    http_response_code($code);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function get_string(string $key, string $default): string
{
    $value = $_GET[$key] ?? $default;
    return is_string($value) ? trim($value) : $default;
}

function get_float(string $key, float $default): float
{
    $value = $_GET[$key] ?? null;
    if ($value === null || $value === '') {
        return $default;
    }
    if (!is_numeric($value)) {
        return $default;
    }
    return (float) $value;
}

function get_int(string $key, int $default): int
{
    $value = $_GET[$key] ?? null;
    if ($value === null || $value === '') {
        return $default;
    }
    if (!is_numeric($value)) {
        return $default;
    }
    return (int) $value;
}

$source = get_string('source', (string) ($config['default_source'] ?? 'scraper'));
if ($source !== 'scraper') {
    respond(400, ['ok' => false, 'error' => 'Only scraper source is supported']);
}

$bankroll = get_float('bankroll', (float) ($config['default_bankroll'] ?? 1000.0));
$minMargin = get_float('min_margin', (float) ($config['default_min_margin'] ?? 0.2));
$minDataQuality = get_float('min_data_quality', 0.0);
$minSourceQuorum = get_int('min_source_quorum', 1);
$slippageBps = get_float('slippage_bps', 0.0);
$rejectionRate = get_float('rejection_rate', 0.0);
$maxEvents = get_int('max_events', 40);
$staleThresholdSec = get_int('stale_threshold_sec', 900);
$sampleCheck = get_int('sample_check', 5);
$includePrematch = get_string('include_prematch', '0') === '1';
$scraperSource = get_string('scraper_source', 'flashscore');
$geoIpCode = strtoupper(get_string('geo_ip_code', 'GB'));
$geoIpSubdivision = strtoupper(get_string('geo_ip_subdivision', 'GBENG'));
$maxBookmakersPerEvent = get_int('max_bookmakers_per_event', 8);
$quorumMinSources = get_int('quorum_min_sources', 2);
$limitsFile = get_string('limits_file', '');
$forceRefresh = get_string('refresh', '0') === '1';

if ($bankroll <= 0 || $bankroll > 100000000) {
    respond(400, ['ok' => false, 'error' => 'Invalid bankroll']);
}
if ($minMargin < 0 || $minMargin > 100) {
    respond(400, ['ok' => false, 'error' => 'Invalid min_margin']);
}
if ($minDataQuality < 0 || $minDataQuality > 1) {
    respond(400, ['ok' => false, 'error' => 'Invalid min_data_quality']);
}
if ($minSourceQuorum < 1 || $minSourceQuorum > 5) {
    respond(400, ['ok' => false, 'error' => 'Invalid min_source_quorum']);
}
if ($slippageBps < 0 || $slippageBps > 2000) {
    respond(400, ['ok' => false, 'error' => 'Invalid slippage_bps']);
}
if ($rejectionRate < 0 || $rejectionRate > 1) {
    respond(400, ['ok' => false, 'error' => 'Invalid rejection_rate']);
}
if ($maxEvents < 1 || $maxEvents > 200) {
    respond(400, ['ok' => false, 'error' => 'Invalid max_events']);
}
if ($staleThresholdSec < 10 || $staleThresholdSec > 86400) {
    respond(400, ['ok' => false, 'error' => 'Invalid stale_threshold_sec']);
}
if ($sampleCheck < 0 || $sampleCheck > 50) {
    respond(400, ['ok' => false, 'error' => 'Invalid sample_check']);
}
if (!in_array($scraperSource, ['flashscore', 'sofascore', 'multi'], true)) {
    respond(400, ['ok' => false, 'error' => 'Invalid scraper_source']);
}
if (!preg_match('/^[A-Z]{2,10}$/', $geoIpCode)) {
    respond(400, ['ok' => false, 'error' => 'Invalid geo_ip_code']);
}
if (!preg_match('/^[A-Z0-9]{2,10}$/', $geoIpSubdivision)) {
    respond(400, ['ok' => false, 'error' => 'Invalid geo_ip_subdivision']);
}
if ($maxBookmakersPerEvent < 1 || $maxBookmakersPerEvent > 30) {
    respond(400, ['ok' => false, 'error' => 'Invalid max_bookmakers_per_event']);
}
if ($quorumMinSources < 1 || $quorumMinSources > 3) {
    respond(400, ['ok' => false, 'error' => 'Invalid quorum_min_sources']);
}
$cacheDir = __DIR__ . '/cache';
if (!is_dir($cacheDir)) {
    @mkdir($cacheDir, 0777, true);
}
$cacheKey = md5(implode('|', [
    $source,
    (string) $bankroll,
    (string) $minMargin,
    (string) $minDataQuality,
    (string) $minSourceQuorum,
    (string) $slippageBps,
    (string) $rejectionRate,
    (string) $maxEvents,
    (string) $staleThresholdSec,
    (string) $sampleCheck,
    $includePrematch ? '1' : '0',
    $scraperSource,
    $geoIpCode,
    $geoIpSubdivision,
    (string) $maxBookmakersPerEvent,
    (string) $quorumMinSources,
    $limitsFile,
]));
$cacheFile = $cacheDir . '/' . $cacheKey . '.json';
$ttl = (int) ($config['cache_ttl_seconds'] ?? 20);

if (!$forceRefresh && is_file($cacheFile) && (time() - filemtime($cacheFile) <= $ttl)) {
    $cached = file_get_contents($cacheFile);
    if ($cached !== false) {
        $payload = json_decode($cached, true);
        if (is_array($payload)) {
            $payload['cached'] = true;
            respond(200, $payload);
        }
    }
}

$projectRoot = realpath(__DIR__ . '/..');
if ($projectRoot === false) {
    respond(500, ['ok' => false, 'error' => 'Project root not found']);
}

$pythonBin = (string) ($config['python_bin'] ?? 'python');
$cmdBase = escapeshellcmd($pythonBin) . ' -m iddiaarb.cli ';
$cmd = $cmdBase
    . 'scrape-scan'
    . ' --scraper-source ' . escapeshellarg($scraperSource)
    . ' --max-events ' . escapeshellarg((string) $maxEvents)
    . ' --stale-threshold-sec ' . escapeshellarg((string) $staleThresholdSec)
    . ' --sample-check ' . escapeshellarg((string) $sampleCheck)
    . ($includePrematch ? ' --include-prematch' : '')
    . ' --geo-ip-code ' . escapeshellarg($geoIpCode)
    . ' --geo-ip-subdivision ' . escapeshellarg($geoIpSubdivision)
    . ' --max-bookmakers-per-event ' . escapeshellarg((string) $maxBookmakersPerEvent)
    . ' --quorum-min-sources ' . escapeshellarg((string) $quorumMinSources)
    . ' --bankroll ' . escapeshellarg((string) $bankroll)
    . ' --min-margin ' . escapeshellarg((string) $minMargin)
    . ' --min-data-quality ' . escapeshellarg((string) $minDataQuality)
    . ' --min-source-quorum ' . escapeshellarg((string) $minSourceQuorum)
    . ' --slippage-bps ' . escapeshellarg((string) $slippageBps)
    . ' --rejection-rate ' . escapeshellarg((string) $rejectionRate)
    . ($limitsFile !== '' ? ' --limits-file ' . escapeshellarg($limitsFile) : '')
    . ' --format json';

$desc = [
    0 => ['pipe', 'r'],
    1 => ['pipe', 'w'],
    2 => ['pipe', 'w'],
];

$proc = proc_open($cmd, $desc, $pipes, $projectRoot);
if (!is_resource($proc)) {
    respond(500, ['ok' => false, 'error' => 'Could not start scanner process']);
}

fclose($pipes[0]);
$stdout = stream_get_contents($pipes[1]);
$stderr = stream_get_contents($pipes[2]);
fclose($pipes[1]);
fclose($pipes[2]);
$exitCode = proc_close($proc);

if ($exitCode !== 0) {
    $errorText = trim($stderr) !== '' ? trim($stderr) : trim((string) $stdout);
    respond(500, [
        'ok' => false,
        'error' => $errorText,
        'exit_code' => $exitCode,
    ]);
}

$decoded = json_decode((string) $stdout, true);
if (!is_array($decoded)) {
    respond(500, [
        'ok' => false,
        'error' => 'Scanner returned non-JSON output',
        'raw' => substr((string) $stdout, 0, 2000),
    ]);
}

$payload = [
    'ok' => true,
    'cached' => false,
    'generated_at' => gmdate('c'),
    'params' => [
        'source' => $source,
        'bankroll' => $bankroll,
        'min_margin' => $minMargin,
        'min_data_quality' => $minDataQuality,
        'min_source_quorum' => $minSourceQuorum,
        'slippage_bps' => $slippageBps,
        'rejection_rate' => $rejectionRate,
        'max_events' => $maxEvents,
        'stale_threshold_sec' => $staleThresholdSec,
        'sample_check' => $sampleCheck,
        'include_prematch' => $includePrematch,
        'scraper_source' => $scraperSource,
        'geo_ip_code' => $geoIpCode,
        'geo_ip_subdivision' => $geoIpSubdivision,
        'max_bookmakers_per_event' => $maxBookmakersPerEvent,
        'quorum_min_sources' => $quorumMinSources,
        'limits_file' => $limitsFile,
    ],
    'scan' => $decoded,
];

@file_put_contents(
    $cacheFile,
    json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
);

respond(200, $payload);
