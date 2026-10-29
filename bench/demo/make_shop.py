"""Write the demo's sample project: a small polyglot shop.

  services/<name>/   12 Python services: app.py, test_app.py, pyproject.toml
  dist/              their packages, <name>-<version>.tar.gz
  lib/, spec/        a Ruby library and its specs
  billing/           a PHP module and its PHPUnit tests
  deploy/            an Ansible inventory (local connections)
  storefront         a link to a Laravel application, if one is given

Usage: python3 make_shop.py <dir> [laravel_dir]
"""
import os
import random
import sys

SERVICES = {
    "cart": "1.4.2", "catalog": "3.1.0", "checkout": "2.2.5", "inventory": "1.9.3",
    "orders": "2.0.1", "payments": "0.9.7", "pricing": "1.3.0", "reviews": "0.4.2",
    "search": "2.7.1", "shipping": "1.1.0", "tax": "1.0.4", "users": "3.0.0",
}

APP = '''"""The {name} service."""


def describe() -> str:
    return "{name} service, version {version}"


def total(prices: list[float], rate: float = 0.2) -> float:
    return round(sum(prices) * (1 + rate), 2)
'''

TEST = '''from app import describe, total


def test_describe():
    assert describe().startswith("{name}")


def test_total():
    assert total([10.0, 5.0], rate=0.1) == 16.5
'''

PYPROJECT = '''[project]
name = "{name}"
version = "{version}"
description = "The {name} service of the shop"
authors = [{{name = "Shop team"}}]
requires-python = ">=3.11"
'''

RUBY = {
    "coupon": ("Applies a percentage coupon to an amount.", "def apply(amount)\n    amount * (100 - percent) / 100.0\n  end",
               "described_class.new(10).apply(200)", "180.0"),
    "invoice": ("Totals the lines of an invoice.", "def total(lines)\n    lines.sum { |line| line[:qty] * line[:price] }\n  end",
                "described_class.new(0).total([{ qty: 2, price: 5 }])", "10"),
    "loyalty": ("Turns spending into loyalty points.", "def points(spent)\n    (spent / 10).floor + percent\n  end",
                "described_class.new(0).points(95)", "9"),
    "refund": ("Computes what a return gives back.", "def amount(paid)\n    paid - (paid * percent / 100.0)\n  end",
               "described_class.new(10).amount(50)", "45.0"),
    "stock": ("Tells whether stock covers an order.", "def covers?(wanted)\n    wanted <= percent\n  end",
              "described_class.new(3).covers?(2)", "true"),
    "voucher": ("Caps a voucher at the order amount.", "def value(order)\n    [order, percent].min\n  end",
                "described_class.new(20).value(15)", "15"),
}

PHP = {
    "Invoice": ("total", "array $lines", "return array_sum($lines);", "[10, 5]", "15"),
    "Discount": ("apply", "float $amount", "return $amount * 0.9;", "100.0", "90.0"),
    "Vat": ("add", "float $amount", "return round($amount * 1.2, 2);", "10.0", "12.0"),
    "Currency": ("cents", "float $amount", "return (int) round($amount * 100);", "1.5", "150"),
    "Receipt": ("lines", "array $items", "return count($items);", "['a', 'b', 'c']", "3"),
    "Refund": ("amount", "float $paid", "return $paid / 2;", "8.0", "4.0"),
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def main():
    root = sys.argv[1]
    rng = random.Random(42)
    for name, version in SERVICES.items():
        values = {"name": name, "version": version}
        write(f"{root}/services/{name}/app.py", APP.format(**values))
        write(f"{root}/services/{name}/test_app.py", TEST.format(**values))
        write(f"{root}/services/{name}/pyproject.toml", PYPROJECT.format(**values))
        os.makedirs(f"{root}/dist", exist_ok=True)
        with open(f"{root}/dist/{name}-{version}.tar.gz", "wb") as f:
            f.write(rng.randbytes(rng.randrange(40_000, 400_000)))

    write(f"{root}/.rubocop.yml", "AllCops:\n  NewCops: enable\n  SuggestExtensions: false\n")
    for name, (doc, method, call, expected) in RUBY.items():
        cls = name.capitalize()
        write(f"{root}/lib/{name}.rb",
              f"# frozen_string_literal: true\n\n# {doc}\nclass {cls}\n  attr_reader :percent\n\n"
              f"  def initialize(percent)\n    @percent = percent\n  end\n\n  {method}\nend\n")
        write(f"{root}/spec/{name}_spec.rb",
              f"# frozen_string_literal: true\n\nrequire_relative '../lib/{name}'\n\n"
              f"RSpec.describe {cls} do\n  it 'works' do\n    expect({call}).to eq({expected})\n  end\nend\n")

    for cls, (method, param, body, arg, expected) in PHP.items():
        write(f"{root}/billing/src/{cls}.php",
              f"<?php\n\nnamespace Billing;\n\nfinal class {cls}\n{{\n    public function {method}({param}): mixed\n"
              f"    {{\n        {body}\n    }}\n}}\n")
        write(f"{root}/billing/tests/{cls}Test.php",
              f"<?php\n\nrequire_once __DIR__ . '/../src/{cls}.php';\n\nuse PHPUnit\\Framework\\TestCase;\n\n"
              f"final class {cls}Test extends TestCase\n{{\n    public function test{method.capitalize()}(): void\n"
              f"    {{\n        $this->assertSame({expected}, (new Billing\\{cls}())->{method}({arg}));\n    }}\n}}\n")

    write(f"{root}/deploy/inventory.ini",
          "[web]\nweb1\nweb2\nweb3\n\n[db]\ndb1\n\n"
          "[all:vars]\nansible_connection=local\nansible_python_interpreter=/usr/bin/python3\n")

    if len(sys.argv) > 2 and not os.path.lexists(f"{root}/storefront"):
        os.symlink(sys.argv[2], f"{root}/storefront")


if __name__ == "__main__":
    main()
