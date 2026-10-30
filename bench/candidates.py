"""Candidate tools, not yet in the panel, measured as panel.py measures it,
in the image of bench/candidates/Dockerfile.

Usage: python3 candidates.py <speedrunner binary> <out_dir>   (env as panel.py)
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel  # noqa: E402

ROOT = panel.ROOT
panel.PATH = ":".join(sorted(glob.glob(f"{ROOT}/cand/*/bin")) + sorted(glob.glob(f"{ROOT}/php/c-*/vendor/bin"))
                      + [f"{ROOT}/php/drupal/vendor/bin", f"{ROOT}/gems-rb/bin",
                         "/usr/local/bin", "/usr/bin", "/bin"])

panel.SAMPLES.update({
    "sample.sql": "SELECT a, b\nFROM t\n",
    "sample.adoc": "= Title\n\nSome *text*.\n",
    "template.yaml": ("AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Bucket:\n"
                      "    Type: AWS::S3::Bucket\n"),
    "dbt/dbt_project.yml": "name: demo\nversion: '1.0'\nprofile: demo\n",
    "dbt/profiles.yml": ("demo:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
                         "      path: demo.duckdb\n"),
    "dbt/models/one.sql": "select 1 as id\n",
})

panel.EXTRA_ENV = {
    "DBT_PROFILES_DIR": "{work}/dbt", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    "DBT_VERSION_CHECK": "false",
    "AIRFLOW_HOME": "{scratch}/airflow", "SAM_CLI_TELEMETRY": "0",
    "OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING": "True",
    "PLATFORMIO_CORE_DIR": "{scratch}/pio", "CONAN_HOME": "{scratch}/conan",
    "PDM_CHECK_UPDATE": "false",
    # The gems Ruby comes with too (minitest, for kamal's activesupport).
    "GEM_PATH": f"{ROOT}/gems-rb:/usr/lib/ruby/gems/3.1.0:/usr/share/rubygems-integration/all",
    "FASTLANE_SKIP_UPDATE_CHECK": "1", "FASTLANE_OPT_OUT_USAGE": "1", "FASTLANE_HIDE_CHANGELOG": "1",
}
panel.CWDS = {"dbt": "{work}/dbt", "symfony": f"{ROOT}/php/symfony", "drupal": f"{ROOT}/php/drupal"}

V = "--version"
panel.PANEL = [
    ("python", "dbt --help", ["dbt", "--help"], None, False),
    ("python", "dbt ls", ["dbt", "ls"], "dbt", True),
    ("python", "airflow version", ["airflow", "version"], None, False),
    ("python", "checkov --version", ["checkov", V], None, False),
    ("python", "cfn-lint template.yaml", ["cfn-lint", "template.yaml"], "work", False),
    ("python", "sam --version", ["sam", V], None, False),
    ("python", "openstack --version", ["openstack", V], None, False),
    ("python", "oci --version", ["oci", V], None, False),
    ("python", "pylint --version", ["pylint", V], None, False),
    ("python", "pylint sample.py", ["pylint", "sample.py"], "work", False),
    ("python", "sqlfluff lint sample.sql", ["sqlfluff", "lint", "--dialect", "ansi", "sample.sql"], "work", True),
    ("python", "pgcli --version", ["pgcli", V], None, False),
    ("python", "mycli --version", ["mycli", V], None, False),
    ("python", "litecli --version", ["litecli", V], None, False),
    ("python", "yt-dlp --version", ["yt-dlp", V], None, False),
    ("python", "meson --version", ["meson", V], None, False),
    ("python", "conan --version", ["conan", V], None, False),
    ("python", "pio --version", ["pio", V], None, False),
    ("python", "mkdocs --version", ["mkdocs", V], None, False),
    ("python", "pipenv --version", ["pipenv", V], None, False),
    ("python", "pdm --version", ["pdm", V], None, False),
    ("python", "hatch --version", ["hatch", V], None, False),
    ("python", "twine --version", ["twine", V], None, False),
    ("python", "bandit --version", ["bandit", V], None, False),
    ("python", "codespell --version", ["codespell", V], None, False),
    ("python", "cookiecutter --version", ["cookiecutter", V], None, False),
    ("python", "copier --version", ["copier", V], None, False),
    ("python", "nox --version", ["nox", V], None, False),
    ("python", "command-not-found sl", ["/usr/lib/command-not-found", "sl"], None, False),
    ("ruby", "fastlane --version", ["fastlane", V], None, True),
    ("ruby", "kamal version", ["kamal", "version"], None, False),
    ("ruby", "cap --version", ["cap", V], None, False),
    ("ruby", "brakeman --version", ["brakeman", V], None, False),
    ("ruby", "standardrb sample.rb", ["standardrb", "sample.rb"], "work", False),
    ("ruby", "asciidoctor sample.adoc", ["asciidoctor", "-o", "-", "sample.adoc"], "work", False),
    ("ruby", "puppet --version", ["puppet", V], None, False),
    ("ruby", "jekyll --version", ["jekyll", V], None, False),
    ("php", "wp --version", ["wp", V, "--allow-root"], None, False),
    ("php", "php bin/console --version", ["php", "bin/console", V], "symfony", False),
    ("php", "php bin/console list", ["php", "bin/console", "list"], "symfony", False),
    ("php", "drush --version", ["drush", V], "drupal", False),
    ("php", "pint --version", ["pint", V], None, False),
    ("php", "infection --version", ["infection", V], None, False),
    ("php", "phpmd --version", ["phpmd", V], None, False),
    ("php", "dep --version", ["dep", V], None, False),
    ("php", "behat --version", ["behat", V], None, False),
    ("php", "pest --version", ["pest", V], None, False),
]

if __name__ == "__main__":
    panel.main()
