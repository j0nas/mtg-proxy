# mtg-proxy

Builds MTG proxy sheets, prints them on the Windows PC's Epson over SSH, and cuts them on a
Silhouette Cameo 5 Alpha, all from the Mac. A bare `mtg-proxy` at a terminal is the wizard the
operator uses (`wizard.py`); `mtg-proxy make|print|cut|…` are the same steps for scripts. The
README is the reference for every setting and the measurements behind it; the Cameo's firmware is
reverse-engineered in `~/Desktop/projects/cut/cameo-re` (`INVESTIGATION.md`, `NOTES.md`).

## Log every encounter, and learn from all of them

`docs/lessons.md` is the log of everything that went wrong or cost time, with the general lessons
drawn from it. Read it before working on building, registration or cutting.

After any encounter (a failed or wrong cut, a misread, a stop that didn't help, a theory that turned
out wrong, a fix that had to be undone), add it there before you finish: what was seen, what you
first thought it was, what it actually was, the general lesson, and what changed. Log the wrong
turns too; they are what the next agent most needs.

Learn in a way that generalizes. A specific failure is an instance of a broader problem; find that
problem and fix it at its root:

- Before fixing, name the class the failure belongs to (for example: the machine that built a sheet
  and the machine that cuts it ran different code) and fix the class (a version check, a contract
  recorded with the artifact and checked where it is used). Don't patch around one bad artifact,
  and don't leave special cases in the code for something that shouldn't exist.
- Check the inputs before explaining the outputs: which code built the artifact, on which machine,
  and what the physical sheet actually is. Measure it directly early; don't theorize from what it
  should be.
- When a new failure resembles an old one in `docs/lessons.md`, ask what they have in common, and
  update the general lessons when the answer is new.
- Make every failure fast and specific: a search with a budget, a stop that says what it saw and
  what to do.

## Working with the machine

- Verify a theory on the machine before building on it; the session logs in `output/cut/` hold
  every reading and reply.
- Physical constraints the operator states (how a laminated sheet must be tacked down, where the
  paper can go) are requirements, not suggestions.
- Before a slow rerun, put in the change that makes the next failure cheap.
