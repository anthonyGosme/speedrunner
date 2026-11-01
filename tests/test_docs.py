"""The docs against the code: every environment variable and subcommand the
code reads is in the user guide, the guide names none that does not exist,
and every relative link and in-page anchor of the Markdown files resolves.

Needs nothing but the repository.

Run: python3 tests/test_docs.py -v
"""
import glob
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE = ["client/src/*.rs", "client/build.rs", "server/*", "install.sh", "shell/*"]
DOCS = ["README.md", "docs/*.md", "packaging/README.md"]


def read(path):
    with open(os.path.join(REPO, path), errors="replace") as f:
        return f.read()


def files(patterns):
    return sorted({os.path.relpath(p, REPO) for pat in patterns
                   for p in glob.glob(os.path.join(REPO, pat)) if os.path.isfile(p)})


def slug(heading):
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(text):
    found = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.M):
        s = slug(heading)
        n = 0
        while (s if n == 0 else f"{s}-{n}") in found:  # GitHub numbers repeats
            n += 1
        found.add(s if n == 0 else f"{s}-{n}")
    return found


class GuideMatchesCode(unittest.TestCase):

    def setUp(self):
        self.guide = read("docs/GUIDE.md")
        self.code = "\n".join(read(p) for p in files(CODE))

    def test_every_variable_is_documented_and_exists(self):
        pattern = r"SPEEDRUNNER_[A-Z0-9_]*[A-Z0-9]"
        in_code, in_guide = set(re.findall(pattern, self.code)), set(re.findall(pattern, self.guide))
        self.assertEqual(sorted(in_code - in_guide), [], "read by the code, missing from the guide")
        self.assertEqual(sorted(in_guide - in_code), [], "in the guide, read by nothing")

    def test_every_subcommand_is_documented(self):
        main = read("client/src/main.rs")
        body = main[main.index("\nfn main("):]
        body = body[:body.index("\nfn ", 1)]
        commands = set(re.findall(r'Some\(b"([a-z][a-z-]*)"\) =>', body))
        self.assertGreaterEqual(len(commands), 9, commands)  # the parsing changed shape
        for command in sorted(commands):
            with self.subTest(command=command):
                self.assertTrue(re.search(rf"speedrunner {re.escape(command)}\b", self.guide),
                                f"speedrunner {command}: not in the guide")


class Links(unittest.TestCase):

    def test_relative_links_and_anchors_resolve(self):
        for doc in files(DOCS):
            text = read(doc)
            # Links outside code spans and fenced blocks.
            prose = re.sub(r"```.*?```|`[^`\n]*`", "", text, flags=re.S)
            for target in re.findall(r"\]\(([^)\s]+)\)", prose):
                if re.match(r"[a-z]+:", target):
                    continue  # https:, mailto:
                path, _, anchor = target.partition("#")
                with self.subTest(doc=doc, link=target):
                    dest = os.path.normpath(os.path.join(os.path.dirname(doc), path)) if path else doc
                    full = os.path.join(REPO, dest)
                    self.assertTrue(os.path.exists(full), f"{doc}: no {dest}")
                    if anchor and dest.endswith(".md"):
                        self.assertIn(anchor, anchors(read(dest)), f"{doc}: no #{anchor} in {dest}")


if __name__ == "__main__":
    unittest.main()
