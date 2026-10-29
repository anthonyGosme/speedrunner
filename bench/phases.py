"""Time the in-process phases of one native `aws` invocation.

Mirrors bin/aws from awscli v1, with timestamps between phases:
import -> create_clidriver() -> driver.main(). Appends one JSON line
to the file named by $SPEEDRUNNER_PHASES_OUT.

Usage: python phases.py <aws args...>
"""
import time

t_start = time.perf_counter()

import json
import os
import sys

# Same top-level env tweak as bin/aws, so the work matches the native script.
if os.environ.get('LC_CTYPE', '') == 'UTF-8':
    os.environ['LC_CTYPE'] = 'en_US.UTF-8'
import awscli.clidriver

t_imported = time.perf_counter()
driver = awscli.clidriver.create_clidriver()
t_driver = time.perf_counter()
try:
    rc = driver.main(sys.argv[1:])
except SystemExit as e:
    # argparse exits from inside main() for --version and usage errors.
    rc = e.code
t_done = time.perf_counter()

with open(os.environ['SPEEDRUNNER_PHASES_OUT'], 'a') as f:
    f.write(json.dumps({
        'args': sys.argv[1:],
        'rc': rc,
        'import_s': t_imported - t_start,
        'create_driver_s': t_driver - t_imported,
        'run_s': t_done - t_driver,
    }) + '\n')
sys.exit(rc)
