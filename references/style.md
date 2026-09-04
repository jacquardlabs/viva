# The register — how a viva draft is written

Read this before you draft (`/viva-write` step 4) and before every rewrite
(`loop.py wait` names it on a `has-work` round). It fixes the *density* of what
you write, the way the type fixes the sections and the attachments fix the
facts. It says nothing about what to argue — that is the human's call at the
gate, and a file that starts to is the drift `PRODUCT.md` warns about.

The rules are the practitioner consensus (sources at the end), not one
reviewer's taste: readers scan rather than read, and the same content at half
the words tested 58% more usable, 124% with a scannable layout and objective
language on top.

## Lead with the point

- **The doc opens on what it decides and for whom.** One or two sentences of
  scope — what this covers, what it does not, who it is for. That is a scope
  line, not a preamble; the preamble is the brief retold.
- **A section's first sentence is its conclusion.** The rest supports it.
  Inverted pyramid: a reader who stops after the first paragraph has the
  answer.
- **A paragraph holds one topic** and its first sentence states it. Three to
  five sentences; past seven, split.
- **A section stands alone.** The reviewer sees one card at a time and a
  retriever returns one chunk, so no "as above," no "now that you have," no
  "with everything configured." A term, prerequisite, or path a section
  depends on is named in that section.

## What the doc carries

- **Decisions, stated as fact.** "Email is the channel." Not "we decided in the
  interview that email is the channel."
- **The reason for a decision a reader could contest, in one sentence.** A
  design doc's job is its trade-offs, and prose does not hide sloppy thinking
  the way a bare bullet does. The reason for a decision nobody would question
  goes.
- **Sources a reader can open.** `config.py:42`, `#170`, a URL. Cite one where
  the reader would want to check the fact.
- **Numbers attached to nouns.** "3 services," "a 2 s timeout," "40–60%." Not
  "a few services," "a short timeout," "significantly," "screamingly fast."
- **One term per concept.** Rename a term mid-doc and the reader's model stops
  compiling. Define an unfamiliar term once, where it first appears. Spell an
  acronym out on first use, and only introduce one that recurs.
- **Commands, paths, flags, and errors in code font.** A procedure shows the
  command in a code block; an example beats a description of one.

## What the doc never carries

- **Provenance.** Who decided, when, and where — the interview, "this
  session," "measured this session," "rejected in the interview." The
  confidence sidecar (`sourced`/`inferred`), the Revision History ledger, and
  the PR body carry that record. The interview is not a source: state what it
  settled and cite nothing for it.
- **Preamble.** The brief, the attachments, and the persona restated before
  the new information. The reader has the brief.
- **Justified bullets.** A bullet followed by prose explaining the bullet.
  "SMS. Rejected: no delivery receipt." is complete — the reason rides in the
  line, not under it.
- **Filler.**

| Pattern | Example | Fix |
|---------|---------|-----|
| Triple-layer | Intro says what the body covers; body covers it; summary repeats it | Keep the body |
| Hedging filler | "it's worth noting", "keep in mind", "it's important to understand" | Delete the phrase, keep the fact |
| Transition | "Now that we've covered X, let's look at Y." | Delete |
| Unjustified why | Explaining a decision nobody questioned | Cut if obvious or cited |
| Caveat stacking | Three warnings before the point | The point, then one caveat |
| Passive hedging | "It can be seen that", "It is recommended that" | Active voice, or delete |
| Weak verbs | "there is a variable that stores", "an error occurs when", "is able to" | "the variable stores", "dividing by zero raises", "can" |
| Long way round | "in order to", "at this point in time", "utilize", "you can access" | "to", "now", "use", "access" |
| Softeners | "simply", "just", "easily", "please" | Delete |
| Self-adjectives | "comprehensive", "robust", "powerful" | The specs are the adjectives |

## Sentences

- **One idea per sentence.** A sentence carrying three "or"s is a list.
- **Active voice, strong verbs, present tense.** Name who does what; "be,"
  "occur," and "happen" are the verbs to replace.
- **Procedures are second person and imperative.** Numbered, one action per
  step, the condition before the instruction: "If the tab is open, close it."
- **No ambiguous "it," "this," "that."** Name the noun.

## Lists and tables

- Numbered for a sequence, bulleted for a set, a table for a comparison.
- Items parallel in grammar, tense, and form; a bullet opens on a verb or a
  noun, never a hedge.
- A sentence ending in a colon introduces every list and table.
- Meaningful column headers, short cells.

## The trim pass

Run it on your own draft before `parse_sections.py` sees it, and on every
section you rewrite. One binary test per sentence: if removing it loses nothing
the reader needs to act, delete it. Delete; do not rephrase, do not fold a cut
sentence into its neighbor, and do not add content while cutting — note a gap
and fill it as its own edit. Then hunt the table above. Then reread at speed:
a sentence you skim past goes.

A loose draft loses 40–60% of its words on this pass and no facts. A pass that
cut under 30% rephrased instead of deleting.

Two readers, two cuts. Cut what a capable reader already knows — what a design
doc is, how a library works, what a term means in general. Keep the local fact
they cannot infer — the flag, the path, the quirk, the number. A doc an agent
will act on is shorter on general knowledge and longer on specifics than one
written for a colleague who sits nearby.

## Where the register stops

- **A `suggestion` is pasted verbatim.** The reviewer's wording is applied
  character for character, however loose it reads — the register never edits
  what the reviewer wrote.
- **A `changes` comment wins.** Asked for more, write more, in this register.
  The human gate decides length; this file decides density.
- **The reviewer's notes are recorded verbatim** in threads and in the ledger.
  The register applies to your prose only.
- **Hunks are code.** `/viva-review` branch B reviews diffs; nothing here
  applies to them.

## Sources

- Google, *Technical Writing One* — words, sentences, lists, paragraphs,
  documents: https://developers.google.com/tech-writing/one
- Google developer documentation style guide, highlights:
  https://developers.google.com/style/highlights
- Microsoft Writing Style Guide, top 10 tips:
  https://learn.microsoft.com/en-us/style-guide/top-10-tips-style-voice
- Nielsen Norman Group, *How Users Read on the Web* (the 58% / 124% figures):
  https://www.nngroup.com/articles/how-users-read-on-the-web/
- Malte Ubl, *Design Docs at Google* (trade-offs are the job):
  https://www.industrialempathy.com/posts/design-docs-at-google/
- Amazon's narrative memo (why a reason is a sentence, not a bullet):
  https://www.cnbc.com/2018/04/23/what-jeff-bezos-learned-from-requiring-6-page-memos-at-amazon.html
- Anthropic, skill authoring best practices (cut what the reader knows):
  https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- kapa.ai, writing documentation for AI (a section stands alone):
  https://docs.kapa.ai/improving/writing-best-practices
