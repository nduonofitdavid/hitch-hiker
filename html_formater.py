import os
import re
import sys
from htmlmin import minify

directory = sys.argv[1]

def format_document(dir: str):
  """Format html documents into a single line"""
  for entry in os.scandir(dir):
    if entry.is_file():
      if not entry.name[-4:] == "html": continue
      with open(entry.path) as file:
        html = file.read()
      one_line = minify(html, remove_comments=True, reduce_empty_attributes=True, remove_optional_attribute_quotes=True, keep_pre=False)
      one_line = re.sub(r"\s+", " ", one_line)
      with open(entry.path, 'w') as file:
        file.write(one_line)
    else:
      format_document(entry.path)

if __name__ == "__main__":
  format_document(directory)