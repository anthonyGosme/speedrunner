#!/usr/bin/env bash
# In the container: copy the working copy mounted at /src to where the VPS
# has it, build the client, install the test fixtures, then run the command
# given (in the copy). New bench results are copied back to /src.
#
#   docker run --rm -it -v "$PWD:/src" speedrunner-dev tests      # the 5 suites
#   docker run --rm -it -v "$PWD:/src" speedrunner-dev <command>  # anything
set -euo pipefail
root=/root/speedrunner repo=/root/speedrunner/repo
# Hidden top-level directories (git, editors, tools) stay out, CI config aside.
rsync -a --delete --include /.github --exclude "/.*" --exclude target /src/ "$repo/"
(cd "$repo/client" && cargo build --release -q)
cp -r "$repo/tests/fixture/hstest" /tmp/hstest-build
"$root/venv-test/bin/pip" install -q --no-deps --force-reinstall /tmp/hstest-build
rm -rf /tmp/hstest-build /tmp/rbtest-build
cp -r "$repo/tests/fixture/rbtest" /tmp/rbtest-build
(cd /tmp/rbtest-build && gem build -q rbtest.gemspec > /dev/null 2>&1 \
    && GEM_HOME=$root/gems-rbtest gem install -q --local --no-document rbtest-0.1.0.gem > /dev/null)

cd "$repo"
before=$(ls bench/results)
export B=$repo/client/target/release/speedrunner P=$root/venv-test/bin:$root/venv-aws/bin
export ASCIINEMA=$root/venv-rec/bin/asciinema  # for bench/demo/record.sh
status=0
if [ "${1:-}" = tests ]; then
    # Every suite runs; the exit status says whether one failed (for CI).
    for t in test_matrix test_shell test_scripts; do
        echo "== $t"; HS_BIN=$B HS_PATH=$P python3 tests/$t.py 2>&1 | tail -3 || status=1
    done
    echo "== ruby"
    RB_GEM_HOME=$root/gems-rbtest HS_BIN=$B HS_PATH=$root/gems-rbtest/bin:$P python3 tests/test_ruby.py 2>&1 | tail -3 || status=1
    echo "== php"
    # The Laravel project is in the Debian image only: optional elsewhere.
    laravel=$root/php/laravel; [ -d "$laravel" ] || laravel=
    PHP_LARAVEL=$laravel HS_BIN=$B HS_PATH=$repo/tests/fixture/phptest/bin:$P python3 tests/test_php.py 2>&1 | tail -3 || status=1
else
    "$@" || status=$?
fi
for d in $(ls bench/results); do
    if ! grep -qx "$d" <<< "$before"; then cp -r "bench/results/$d" /src/bench/results/; fi
done
exit $status
