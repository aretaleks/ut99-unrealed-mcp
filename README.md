# UnrealEd MCP for UT99: InstaGib CTF map generator

An [MCP](https://modelcontextprotocol.io) server plus a [Claude Code](https://claude.com/claude-code) skill that
let Claude **design, check and build Unreal Tournament (UT99 v469) maps**, focused on competitive **InstaGib CTF**
(5v5 pugs and cups).

Claude describes a map as CSG brushes, zones, lights, spawns and flags. The server then:
- previews the map as wireframe images;
- checks zoning and BSP correctness;
- **builds the `.unr` headlessly with UCC**, automatically fixing zone bleeding and BSP errors;
- **scores the gameplay** against the iCTF community's design rules and measurements of the community-liked map pool (FragMath pug data and the PassionateGaming cup pool);
- **reviews, fixes, edits and remakes existing maps** (`/review-map`, `/fix-map`, `/edit-map`, `/remake-map`), editing without re-creating them (patch mode): the original stays untouched, and edits are verified against the author's own build;
- **checks texture alignment** (seams, steps at wall corners, stretched or mirrored textures) on generated and existing maps.

## What's in the repository

| Path | Contents |
|---|---|
| `UnrealEdMCP/` | The MCP server (Python): map model and T3D writer, previews, voxel and engine-probe analysis, auto-fix, gameplay metrics and rules, T3D importer, UCC integration. |
| `UnrealEdMCP/GUIDE.md` | **Full documentation**: setup, workflow, every tool, the design rules, the checks, the build pipeline, the research, troubleshooting. |
| `UnrealEdMCP/research/` | Map-pool measurements, reference ranges used by the checks, and FragMath source data. |
| `.claude/skills/ictf-map-design/` | The design guide Claude follows when making iCTF maps. |
| `.claude/skills/new-map/`, `review-map/`, `fix-map/`, `edit-map/`, `remake-map/` | The `/new-map`, `/review-map`, `/fix-map`, `/edit-map` and `/remake-map` commands. |
| `.mcp.json.example` | Template for registering the server with Claude Code. |

No game files are included. You need your own UT99 v469 install.

## Quick start

1. Use a UT99 **v469** install for mapping (469e tested). A separate install is best, so builds never touch the one you play on.
2. Put this repository in the **root of that install**, next to `System\`, `Maps\` and `Textures\`. The `.gitignore` ignores all game files.
   ```
   cd C:\Games\UT469-mapping
   git init
   git remote add origin https://github.com/<you>/<this-repo>.git
   git pull origin main
   ```
3. Install the Python packages (Python 3.12, Windows):
   ```
   python -m pip install "mcp>=1.2,<2" pillow numpy
   ```
4. Copy `.mcp.json.example` to `.mcp.json` and set the paths to your `python.exe` and `UnrealEdMCP\server.py`.
5. Start Claude Code in the UT folder and approve the `unrealed` server. Then ask, for example:
   > *Design an iCTF map in the style of Klondike using the ictf-map-design skill.*

Generated maps land in `Maps\<name>.unr`, ready to open in UnrealEd.

See **[UnrealEdMCP/GUIDE.md](UnrealEdMCP/GUIDE.md)** for everything else.

## Status

The project improves one generated map at a time. Each review by an experienced iCTF player becomes a new rule or check, measured against the map pool first. The current rules come from the reviews of CTF-Coldrush, Motherlode and Bastion (see the history in the guide). Generated maps still need fine-tuning by hand in UnrealEd.

## Contributing

Claude Code sessions: see [CLAUDE.md](CLAUDE.md) for how the project is improved and its hard rules.


Feedback on generated maps, rule proposals, bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT (see `LICENSE`). Unreal Tournament and UnrealEd are trademarks of Epic Games. This project ships no game content.
