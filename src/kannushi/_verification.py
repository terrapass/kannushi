from pathlib import Path
from enum import Enum
from io import StringIO
from itertools import zip_longest

from._rendering import TARGET_ENCODING

#
# Types
#

class TargetFileStatus(int, Enum):
    CURRENT  = 0
    MODIFIED = 1
    MISSING  = 2

#
# Interface
#

def verification_render_handler(target_file_path: Path, rendered_content: str) -> TargetFileStatus:
    if not target_file_path.is_file():
        return TargetFileStatus.MISSING
    with StringIO(rendered_content) as rendered_content_stream, \
        open(target_file_path, 'r', encoding=TARGET_ENCODING) as target_file:
        is_target_file_current = all(map(lambda line_pair: line_pair[0] == line_pair[1], zip_longest(rendered_content_stream, target_file)))
    return TargetFileStatus.CURRENT if is_target_file_current else TargetFileStatus.MODIFIED
