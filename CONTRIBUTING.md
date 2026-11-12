# Contributing

Thanks for helping. This project improves one map review at a time: experienced players say what's wrong with a generated map, and that becomes a rule or a check. You don't need to code to contribute.

## Ways to help

| You have… | Do this |
|---|---|
| Played or looked at a **generated map** and have feedback | Open an issue: **Feedback on a generated map** |
| An idea for a **design rule** ("flag rooms need at least 3 entrances") | Open an issue: **Rule proposal** |
| Found a **bug**: a tool error, a broken build, a wrong check | Open an issue: **Bug report** |
| A **fix or feature** in code, a skill, or the docs | Open a pull request (see below) |
| **Data**: stats, heatmaps, map rankings, the exact map versions your community plays | Open an issue first, so we can agree the format |

## How rules get in

Rules steer what Claude builds for everyone, so each one needs a source. Every rule in the skill is tagged:

- **[community]**: stated by experienced players for a reason they can explain, e.g. "flag-room spawns cause 0-0 overtimes". These win when sources disagree.
- **[data]**: measured on the community map pool (`UnrealEdMCP/research/`) and checked against player preference.
- **[suggestion]**: reasonable practice that hasn't been verified yet.

A good rule proposal names the problem, a map where it happens, a map that gets it right, and *why* it matters in play. Where a rule can be measured, it's calibrated on the pool before it becomes an automatic check, so it doesn't fail maps players like. See "Turning feedback into a rule" in [UnrealEdMCP/GUIDE.md](UnrealEdMCP/GUIDE.md).

## Pull requests

1. Fork the repository and create a branch.
2. Keep each PR to one change, and say why in the description.
3. If you changed a tool, run it on at least one generated project and one pool map and paste the result (e.g. `build_map`, `map_metrics`, `review_map` output).
4. If you changed a check or a rule, include its effect on the pool: which maps pass or fail before and after.
5. Update the docs: `UnrealEdMCP/GUIDE.md` (the tool table, the relevant section, the history), and the skill if the rule changes.

`main` is protected: changes land through reviewed pull requests.

### Reviewed with extra care
- **Code** (`UnrealEdMCP/*.py`) runs on everyone's machine and starts UCC and Python. No network calls, no file access outside the UT folder and its `UnrealEdMCP` folders, no new dependencies without discussion.
- **Skills** (`.claude/skills/*/SKILL.md`) are instructions Claude follows for everyone. Changes must stay about map design and the tools, and must cite their source.
- **Research data** must come from public sources. Don't add personal data beyond what the source already publishes.

## What not to contribute
- Game files: maps, textures or packages you don't own the rights to. The repository ships no game content.
- Generated `.unr` maps. Share those in your community channels; the repo holds the tools.

## Setup
See the [README](README.md) quick start and section 1 of the [guide](UnrealEdMCP/GUIDE.md). Use a separate UT99 v469 install for mapping, so builds never touch the one you play on.
