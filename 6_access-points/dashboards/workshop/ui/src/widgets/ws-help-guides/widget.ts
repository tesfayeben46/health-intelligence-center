/** ws-help-guides: the workshop Help page as a single article:
 *  one reading column with a sticky table of contents,
 *  anchored sections, flowing numbered steps (not cards), callouts, section
 *  prev/next, and the policies (Code of Conduct + Data Use Agreement) folded in
 *  as the closing chapter. `frameless` so it is one article surface, not a card.
 *  Static content, no fetch. Reads ctx.theme for ink; accents from --navy-*.
 *  Visible copy uses plain punctuation (no em dashes / arrows) per the app-wide
 *  text rule. */
import type { WidgetDef } from '../../runtime/types';

type Step = { lead: string; body: string };
type Note = { kind: 'tip' | 'note'; text: string };
type Section = { id: string; nav: string; title: string; intro: string; steps?: Step[]; note?: Note; prose?: string[] };

const SECTIONS: Section[] = [
  {
    id: 'groups', nav: 'Create a group',
    title: 'Create a group',
    intro: 'Groups are invite-only spaces where a team shares work at the shared tier. Anything published to a group becomes visible to every member, so a group is how a project or dataset reaches collaborators without going fully public.',
    steps: [
      { lead: 'Open the Workspace', body: 'Go to the Workspace page and find the groups area. The groups you belong to sit alongside a Discover panel listing every other group.' },
      { lead: 'Start a new group', body: 'In Discover, choose Create a group to open the inline form.' },
      { lead: 'Name it and describe its focus', body: 'Give it a clear name (for example, Malaria modelling network) and a one-line description of what it works on, then create it. You become its owner.' },
      { lead: 'Grow membership by approval', body: 'Joining is invite-only, so there is no instant join. Others request to join from Discover, and you, the owner, approve them.' },
    ],
    note: { kind: 'note', text: 'Invite-only by design: membership is always approved by the owner, so a group only ever contains people the owner has admitted.' },
  },
  {
    id: 'projects', nav: 'Create a project',
    title: 'Create a project',
    intro: 'A project is the home for a body of work: its files, linked datasets, models and code. It is also the unit that gets reviewed and published, so most of your work lives inside a project.',
    steps: [
      { lead: 'Open the Projects page', body: 'It lists the projects you own or have been given access to.' },
      { lead: 'Start a new project', body: 'Choose New project to open the create dialog.' },
      { lead: 'Name it and set visibility', body: 'Give it a name (for example, Malaria early-warning system) and an optional description, then choose Private (only you) or Public (everyone in the workshop). Creating it provisions a private file space and an experiment for its model runs.' },
      { lead: 'Open it and get to work', body: 'From the project detail you get a JupyterLab workspace plus the project files, datasets, models and artifacts. You can share it with a specific group, or the whole workshop, later from the project visibility control.' },
    ],
  },
  {
    id: 'datasets', nav: 'Link datasets',
    title: 'Link datasets to a project',
    intro: 'Datasets are generated independently of projects and then linked where they are used. Because a dataset is independent, the same one can belong to several projects.',
    steps: [
      { lead: 'Generate a dataset', body: 'On the Datasets page, use the dataset builder. Pick a template or write SQL, and choose a tier (personal, shared or public). Every query runs through the one governed, policy-checked SQL path.' },
      { lead: 'Link it as you create it', body: 'The builder lets you pick a project to link the new dataset straight into, or you can leave it unlinked and decide later.' },
      { lead: 'Or link it later from the project', body: 'Open a project, choose Link datasets, and link any of your datasets into it.' },
      { lead: 'Use it in your workspace', body: 'Linked datasets are copied into the project files and appear in its JupyterLab, ready to load in a notebook.' },
    ],
    note: { kind: 'tip', text: 'Every dataset query goes through the governed SQL path, so a dataset always carries its provenance and respects your role scopes, whichever project uses it.' },
  },
  {
    id: 'review', nav: 'Submission and approval',
    title: 'Submission and approval',
    intro: 'Finished work travels from a project, through peer review, to a published public contribution. The project is the unit of review.',
    steps: [
      { lead: 'Get the project ready', body: 'Aim to include the essentials: an inference script, a model artifact or notebook, a train script when the project has a model, and a README or model card.' },
      { lead: 'Submit for review', body: 'From the project detail, choose Submit for review. An automated essential-files check runs and shows what it found. You can submit even if some essentials are missing, since the checklist only warns.' },
      { lead: 'A reviewer opens and runs it', body: 'A human reviewer opens the project, runs its notebooks, and either approves it or requests changes. It shows as in review until they decide.' },
      { lead: 'Approved becomes public', body: 'On approval the project graduates to the public tier and is visible across the workshop. If changes were requested, address them and re-submit.' },
    ],
  },
  {
    id: 'policies', nav: 'Policies and data use',
    title: 'Policies and data use',
    intro: 'This portal is for collaborative learning and constructive contribution. Researchers, developers, students and programme staff work here side by side, and a few commitments keep the shared space safe and trustworthy.',
    prose: [
      '<strong>Be constructive in review.</strong> Peer review decides what reaches the shared and public tiers. Critique the work, never the person, and say what would make a submission acceptable.',
      '<strong>Attribute and cite.</strong> Datasets and models carry licenses and citations. Honour them when you fork, extend or publish derived work.',
      '<strong>Share transparently.</strong> Describe your data provenance and limitations, and do not present synthetic or exploratory results as validated findings.',
      '<strong>No re-identification.</strong> Personal data, even when k-anonymised, must be handled with care and never combined with other sources to re-identify individuals. This is the hard line.',
      '<strong>Purpose limitation.</strong> Use data only for the research purpose your access was granted for. New purposes need a new request through governance.',
      '<strong>No exfiltration.</strong> Take data out only through the governed export and download paths, which record provenance and enforce your scopes.',
      '<strong>Report incidents.</strong> Suspected leaks, mis-scoped access or re-identification risks go to the data-governance team immediately.',
    ],
    note: { kind: 'note', text: 'Enforcement is technical as well as procedural: the gateway, OPA policies and row-level scopes apply these rules on every request, and violations are visible in the audit trail.' },
  },
];

const def: WidgetDef = {
  id: 'ws-help-guides',
  reactsTo: [],
  render(ctx) {
    const { theme } = ctx;

    const stepList = (steps: Step[]): string => `
      <ol class="hg-steps">
        ${steps.map(s => `<li><strong>${s.lead}.</strong> ${s.body}</li>`).join('')}
      </ol>`;
    const prose = (paras: string[]): string =>
      paras.map(p => `<p class="hg-p">${p}</p>`).join('');
    const callout = (n: Note): string => `
      <aside class="hg-callout hg-${n.kind}">
        <span class="hg-callout-tag">${n.kind === 'tip' ? 'Tip' : 'Good to know'}</span>
        <p>${n.text}</p>
      </aside>`;

    const section = (s: Section, i: number): string => {
      const prev = SECTIONS[i - 1];
      const next = SECTIONS[i + 1];
      const nav = (prev || next) ? `
        <nav class="hg-pn" aria-label="Section navigation">
          ${prev ? `<a class="hg-pn-a hg-pn-prev" href="#${prev.id}"><span class="hg-pn-lbl">Previous</span><span class="hg-pn-t">${prev.title}</span></a>` : '<span></span>'}
          ${next ? `<a class="hg-pn-a hg-pn-next" href="#${next.id}"><span class="hg-pn-lbl">Next</span><span class="hg-pn-t">${next.title}</span></a>` : '<span></span>'}
        </nav>` : '';
      return `
        <section class="hg-section" id="${s.id}">
          <h2 class="hg-h2">${s.title}</h2>
          <p class="hg-intro">${s.intro}</p>
          ${s.steps ? stepList(s.steps) : ''}
          ${s.prose ? prose(s.prose) : ''}
          ${s.note ? callout(s.note) : ''}
          ${nav}
        </section>`;
    };

    const toc = `
      <nav class="hg-toc" aria-label="Contents">
        <p class="hg-toc-h">On this page</p>
        <ol class="hg-toc-list">
          ${SECTIONS.map(s => `<li><a href="#${s.id}">${s.nav}</a></li>`).join('')}
        </ol>
      </nav>`;

    ctx.el.innerHTML = `
      <style>
        /* Flatten the dashboard card frame for this article: no border, no
         * shadow, no card padding, so the guide reads as plain page content
         * (owner: remove the heavy card style around the whole content area). */
        .widget-card:has(.hg-wrap) {
          background: transparent !important; border: none !important;
          box-shadow: none !important; padding: 0 !important; overflow: visible !important;
        }
        .hg-wrap {
          display: grid; gap: 3rem;
          grid-template-columns: minmax(0, 1fr) 208px;
          align-items: start; scroll-behavior: smooth;
        }
        .hg-article { min-width: 0; }
        .hg-hero { margin: 0 0 2rem; }
        .hg-eyebrow {
          display: block; font-family: var(--font-sans);
          font-size: 0.72rem; font-weight: 700; letter-spacing: 0.1em;
          text-transform: uppercase; color: var(--navy-500); margin: 0 0 0.55rem;
        }
        .hg-h1 {
          font-family: var(--font-serif); font-weight: 600;
          font-size: 2.15rem; line-height: 1.1; letter-spacing: -0.015em;
          color: ${theme.ink[0]}; margin: 0 0 0.7rem; text-wrap: balance;
        }
        .hg-lede { font-size: 1.05rem; line-height: 1.62; color: ${theme.ink[1]}; margin: 0; max-width: 82ch; }
        .hg-section { padding: 1.9rem 0; border-top: 1px solid var(--border); scroll-margin-top: 1.5rem; }
        .hg-section:first-of-type { border-top: none; padding-top: 0.4rem; }
        .hg-h2 {
          font-family: var(--font-serif); font-weight: 600;
          font-size: 1.5rem; line-height: 1.15; color: ${theme.ink[0]};
          margin: 0 0 0.7rem; letter-spacing: -0.01em;
        }
        .hg-intro { font-size: 0.98rem; line-height: 1.62; color: ${theme.ink[1]}; margin: 0 0 1rem; }
        .hg-steps { margin: 0; padding: 0 0 0 1.4rem; display: flex; flex-direction: column; gap: 0.7rem; }
        .hg-steps li { font-size: 0.92rem; line-height: 1.6; color: ${theme.ink[1]}; padding-left: 0.25rem; }
        .hg-steps li::marker { color: var(--navy-500); font-weight: 700; font-variant-numeric: tabular-nums; }
        .hg-steps strong, .hg-p strong { color: ${theme.ink[0]}; font-weight: 700; }
        .hg-p { font-size: 0.92rem; line-height: 1.62; color: ${theme.ink[1]}; margin: 0 0 0.65rem; }
        .hg-callout {
          margin: 1.15rem 0 0; padding: 0.85rem 1rem; border-radius: 12px;
          background: var(--navy-50); border-left: 3px solid var(--navy-500);
        }
        .hg-callout.hg-tip { background: var(--good-50, #eef7f1); border-left-color: var(--good, #1f7a4f); }
        .hg-callout-tag {
          display: inline-block; font-family: var(--font-sans); font-size: 0.66rem;
          font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
          color: var(--navy-700); margin: 0 0 0.25rem;
        }
        .hg-tip .hg-callout-tag { color: var(--good, #1f7a4f); }
        .hg-callout p { margin: 0; font-size: 0.86rem; line-height: 1.55; color: ${theme.ink[1]}; }
        .hg-pn { display: flex; justify-content: space-between; gap: 1rem; margin: 1.5rem 0 0; }
        .hg-pn-a { text-decoration: none; display: flex; flex-direction: column; max-width: 48%; }
        .hg-pn-next { text-align: right; margin-left: auto; }
        .hg-pn-lbl { font-family: var(--font-sans); font-size: 0.68rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: ${theme.ink[3]}; }
        .hg-pn-t { font-size: 0.9rem; font-weight: 600; color: var(--navy-700); }
        .hg-pn-a:hover .hg-pn-t { text-decoration: underline; }
        .hg-toc { position: sticky; top: 1rem; align-self: start; }
        .hg-toc-h { font-family: var(--font-sans); font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: ${theme.ink[3]}; margin: 0 0 0.6rem; }
        .hg-toc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.15rem; border-left: 1px solid var(--border); }
        .hg-toc-list a { display: block; padding: 0.3rem 0 0.3rem 0.9rem; margin-left: -1px; border-left: 2px solid transparent; font-size: 0.82rem; line-height: 1.35; color: ${theme.ink[2]}; text-decoration: none; }
        .hg-toc-list a:hover { color: var(--navy-700); border-left-color: var(--navy-300, var(--navy-500)); }
        .hg-mobiletoc { display: none; }
        @media (max-width: 860px) {
          .hg-wrap { grid-template-columns: minmax(0, 1fr); gap: 1.2rem; }
          .hg-toc { display: none; }
          .hg-mobiletoc { display: block; margin: 0 0 0.5rem; border: 1px solid var(--border); border-radius: 10px; padding: 0.5rem 0.85rem; }
          .hg-mobiletoc summary { font-family: var(--font-sans); font-size: 0.78rem; font-weight: 700; color: ${theme.ink[1]}; cursor: pointer; }
          .hg-mobiletoc ol { margin: 0.5rem 0 0.2rem; padding-left: 1.1rem; display: flex; flex-direction: column; gap: 0.25rem; }
          .hg-mobiletoc a { font-size: 0.85rem; color: var(--navy-700); text-decoration: none; }
        }
      </style>
      <div class="hg-wrap">
        <article class="hg-article">
          <header class="hg-hero">
            <span class="hg-eyebrow">Workshop guide</span>
            <h1 class="hg-h1">How the workshop works</h1>
            <p class="hg-lede">The workshop is where teams turn warehouse data into datasets, models and published findings. Work flows through groups you collaborate in, projects that hold your files and models, and a review step that promotes finished work to the whole community. This guide walks through each part, and closes with the policies that keep the shared space safe.</p>
          </header>
          <details class="hg-mobiletoc">
            <summary>On this page</summary>
            <ol>${SECTIONS.map(s => `<li><a href="#${s.id}">${s.nav}</a></li>`).join('')}</ol>
          </details>
          ${SECTIONS.map(section).join('')}
        </article>
        ${toc}
      </div>`;
  },
};
export default def;
