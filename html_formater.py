"""Minify HTML files from the command line while preserving preformatted content."""

import os
import argparse
from htmlmin import minify

def format_document(dir: str):
  """Recursively minify HTML files, preserving preformatted text and skipping symlinks."""
  for entry in os.scandir(dir):
    if entry.is_symlink():
      continue
    if entry.is_file():
      if not entry.name.lower().endswith(".html"): continue
      with open(entry.path, encoding="utf-8") as file:
        html = file.read()
      one_line = minify(html, remove_comments=True, reduce_empty_attributes=True, keep_pre=True)
      with open(entry.path, 'w', encoding="utf-8") as file:
        file.write(one_line)
    elif entry.is_dir():
      format_document(entry.path)

def main(argv=None):
  """Validate the directory argument and minify its HTML files."""
  parser = argparse.ArgumentParser(description="Minify HTML files in a directory, preserving preformatted text.")
  parser.add_argument("directory")
  args = parser.parse_args(argv)
  if not os.path.isdir(args.directory):
    parser.error("directory must be an existing directory")
  format_document(args.directory)

if __name__ == "__main__":
  main()
