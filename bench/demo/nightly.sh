#!/usr/bin/env bash
# Demo: the nightly checks of a small polyglot shop (see make_shop.py), as a
# plain bash script. Each command shows as it runs (trace.sh).
#
#   ./nightly.sh                     native
#   speedrunner exec ./nightly.sh    the same script, preloaded
#
# Env: SHOP (the project, default ./shop); ANSIBLE_INVENTORY, and an AWS
# profile whose endpoint is an S3 to upload to (record.sh sets them up).
trace=$(cd "$(dirname "$0")" && pwd)/trace.sh
cd "${SHOP:-shop}" || exit 1  # before the trace starts: a temporary path, not worth showing
source "$trace"

for svc in cart catalog checkout inventory orders payments pricing reviews search shipping tax users; do
  black --check "services/$svc"
  pytest -q "services/$svc"
  poetry check -C "services/$svc"
done

for f in lib/*.rb; do rubocop "$f"; done
for spec in spec/*_spec.rb; do rspec "$spec"; done

phpunit --colors billing/tests
cd storefront
php artisan route:list
php artisan about
cd ..

for env in dev staging prod; do aws configure list --profile $env; done
for host in web1 web2 web3 db1; do ansible-inventory --host $host; done

for svc in cart catalog checkout inventory orders payments pricing reviews search shipping tax users; do
  aws s3 cp dist/$svc-*.tar.gz s3://shop-artifacts/nightly/
  http --offline POST deploy.shop.example/v1/releases service=$svc version=nightly
done
aws s3 ls s3://shop-artifacts/nightly/
ansible web -m debug -a msg=nightly:released
ansible db -m debug -a msg=nightly:migrated

{ set +x; } > /dev/null 2>&1  # its own trace goes to stdout (trace.sh)
finish
