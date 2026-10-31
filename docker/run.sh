#!/usr/bin/env bash
# In the container: copy the working copy mounted at /src to where the VPS
# has it, build the client (or take SPEEDRUNNER_BIN, a prebuilt one), install
# the test fixtures this image can run, then run the suites or command given
# (in the copy). New bench results are copied back to /src.
#
#   docker run --rm -t -v "$PWD:/src" speedrunner-dev tests        # the 5 suites
#   docker run --rm -t -v "$PWD:/src" IMAGE suites matrix ruby      # some of them
#   docker run --rm -t -v "$PWD:/src" speedrunner-dev <command>    # anything
#
# Suites: matrix, shell, scripts (Python), ruby, php, node, stress (races). SR_ROOT: where the
# venvs and gems are (default /root/speedrunner).
set -euo pipefail
root=${SR_ROOT:-/root/speedrunner} repo=${SR_ROOT:-/root/speedrunner}/repo
# Hidden top-level directories (git, editors, tools) stay out, CI config aside.
mkdir -p "$repo"
rsync -a --delete --include /.github --exclude "/.*" --exclude target /src/ "$repo/"
if [ -n "${SPEEDRUNNER_BIN:-}" ]; then
    B=$SPEEDRUNNER_BIN
else
    (cd "$repo/client" && cargo build --release -q)
    B=$repo/client/target/release/speedrunner
fi
tmp=$(mktemp -d)
if [ -x "$root/venv-test/bin/pip" ]; then
    cp -r "$repo/tests/fixture/hstest" "$tmp/hstest"
    "$root/venv-test/bin/pip" install -q --disable-pip-version-check --no-deps --force-reinstall "$tmp/hstest"
fi
if command -v gem > /dev/null; then
    cp -r "$repo/tests/fixture/rbtest" "$tmp/rbtest"
    (cd "$tmp/rbtest" && gem build -q rbtest.gemspec > /dev/null 2>&1 \
        && GEM_HOME=$root/gems-rbtest gem install -q --local --no-document rbtest-0.1.0.gem > /dev/null)
fi
rm -rf "$tmp"

cd "$repo"
before=$(ls bench/results)
export B P=$root/venv-test/bin:$root/venv-aws/bin
export ASCIINEMA=$root/venv-rec/bin/asciinema  # for bench/demo/record.sh
# The Laravel project is in the Debian image only: optional elsewhere.
laravel=$root/php/laravel; [ -d "$laravel" ] || laravel=

run_suite() {
    case $1 in
        matrix|shell|scripts)
            HS_BIN=$B HS_PATH=$P python3 "tests/test_$1.py" ;;
        ruby)
            RB_GEM_HOME=$root/gems-rbtest HS_BIN=$B HS_PATH=$root/gems-rbtest/bin:$P \
                python3 tests/test_ruby.py ;;
        php)
            PHP_LARAVEL=$laravel HS_BIN=$B HS_PATH=$repo/tests/fixture/phptest/bin:$P \
                python3 tests/test_php.py ;;
        node)
            HS_BIN=$B HS_PATH=$repo/tests/fixture/nodetest/bin:$P python3 tests/test_node.py ;;
        stress)  # every runtime this image has
            RB_GEM_HOME=$root/gems-rbtest HS_BIN=$B \
                HS_PATH=$root/gems-rbtest/bin:$repo/tests/fixture/phptest/bin:$repo/tests/fixture/nodetest/bin:$P \
                python3 tests/test_stress.py ;;
        *) echo "unknown suite $1"; return 2 ;;
    esac
}

# Its summary; on a failure, what failed and why too.
suite() {
    echo "== $1"
    local out rc=0
    out=$(mktemp)
    run_suite "$1" > "$out" 2>&1 || rc=$?
    if [ $rc = 0 ]; then
        grep -a "^stress:" "$out" || true  # the stress suite's counts
        tail -3 "$out"
    else
        grep -A 40 -E "^(FAIL|ERROR):" "$out" | head -200 || true
        tail -3 "$out"
    fi
    rm -f "$out"
    return $rc
}

status=0
case "${1:-}" in
    tests) set -- suites matrix shell scripts ruby php ;;
esac
if [ "${1:-}" = suites ]; then
    shift
    # Every suite runs; the exit status says whether one failed (for CI).
    for s in "$@"; do suite "$s" || status=1; done
else
    "$@" || status=$?
fi
for d in $(ls bench/results); do
    if ! grep -qx "$d" <<< "$before"; then cp -r "bench/results/$d" /src/bench/results/; fi
done
exit $status
