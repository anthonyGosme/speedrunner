<?php
// A minimal PSR-4 autoloader, as Composer's would load the classes.
spl_autoload_register(function (string $class): void {
    $prefix = 'Phptest\\';
    if (str_starts_with($class, $prefix)) {
        require __DIR__ . '/' . str_replace('\\', '/', substr($class, strlen($prefix))) . '.php';
    }
});
