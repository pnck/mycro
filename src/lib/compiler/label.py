# Label fixup abstraction for MYCRO compiler bytecode emitter
from bytecode import MacroError


class Label:
    """Forward branch label with backpatching fixup list."""

    def __init__(self, name=""):
        self.name = name
        self.target = None
        self.fixups = []

    def mark(self, compiler):
        """Mark this label at the current bytecode position and resolve all pending fixups."""
        pos = len(compiler._bc)
        self.target = pos
        for patch_pos in self.fixups:
            off = pos - (patch_pos + 2)
            if off < 0:
                raise MacroError("Negative branch offset", patch_pos)
            if off > 65535:
                raise MacroError("Branch offset exceeds 65535", patch_pos)
            compiler._patch_u16(patch_pos, off)
        self.fixups.clear()

    def add_fixup(self, patch_pos, compiler):
        """Add a fixup for this label. If label is already marked, patch immediately."""
        if self.target is not None:
            off = self.target - (patch_pos + 2)
            if off < 0:
                raise MacroError("Negative branch offset", patch_pos)
            if off > 65535:
                raise MacroError("Branch offset exceeds 65535", patch_pos)
            compiler._patch_u16(patch_pos, off)
        else:
            self.fixups.append(patch_pos)
