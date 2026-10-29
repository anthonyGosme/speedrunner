<?php

namespace Phptest;

final class Failing
{
    public static function run(): void
    {
        try {
            throw new \LogicException('inner');
        } catch (\LogicException $e) {
            throw new \RuntimeException('phptest failure', 3, $e);
        }
    }
}
