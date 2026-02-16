<?php
declare(strict_types=1);

return [
    'python_bin' => getenv('PYTHON_BIN') ?: 'python',
    'default_source' => 'scraper',
    'default_bankroll' => 1000.0,
    'default_min_margin' => 0.2,
    'cache_ttl_seconds' => 20,
];
