---
icon: material/account
---

# Registering and Updating a Service

## What is this?

This is the de.NBI & ELIXIR-DE Service Registration Platform. Use it to register your
bioinformatics service for inclusion in the [de.NBI services catalogue](https://www.denbi.de/services),
and to keep the registration up to date.

This guide is for service owners using the web forms. To work with your registration from a script, see [Using the API](api-guide.md).

---

## Registering a New Service

1. Go to **/register/** and fill in the form. All fields marked **(\*)** are required.

    The form features a **sidebar on the right** with:

    - Quick navigation links to jump between sections (A-G)
    - A progress bar showing required field completion percentage
    - Contact information for support

2. Work through each section:

    | Section (as shown on the form)                  | What you need                                                                  |
    | ----------------------------------------------- | ------------------------------------------------------------------------------ |
    | A — General Information                         | Today's date, your name and affiliation, and whether to also register the service as an ELIXIR-DE service |
    | B — Service Master Data                         | Service name, description, year, category, EDAM annotations, publications, optional logo |
    | C — Responsibilities & Responsiveness           | Responsible PI(s), host institute, service centre, public and internal contact |
    | D — Websites & Links                            | Service website, terms of use, license, optional repository and registry links |
    | E — Key Performance Indicators (KPIs)           | Whether KPI monitoring is in place, start year (if applicable)                 |
    | F — Search Tags, Discoverability & Outreach     | Keywords for uncited usage and for discoverability, survey participation, comments |
    | G — Data Protection & Consent                   | Data protection and outreach consent (required to submit)                      |

    Field-by-field help is in [Field Help](#field-help) below.

3. Tick the CAPTCHA checkbox at the end of the form, if the form shows one, and click **Submit Registration**.

4. **You will receive a confirmation email** at the address you provided as internal contact, acknowledging that your registration was received.

5. **Save your API key immediately.** On the confirmation page you will see a box containing your unique API key. This key:
    - Is shown **exactly once** — it will not be emailed to you.
    - Is required to edit your submission later.
    - If lost, see [Lost Your API Key?](#lost-your-api-key).

### Formatting the description

When Markdown descriptions are enabled on the site, the service description supports a small subset of Markdown and the field becomes a small editor with two tabs, **Write** and **Preview** (the same editor is used on the edit form):

- **Write** is the normal text box.
- **Preview** replaces the text box, in place, with the description as it will appear in the catalogue. It shows "Loading preview…" while the server renders it and "Nothing to preview." when the box is empty. The preview is rendered again whenever you have changed the text since the last preview; switching back and forth without editing reuses the last result. Switch back to **Write** to keep editing.
- With the keyboard, focus a tab and use the Left/Right arrow keys (or Home/End) to switch between **Write** and **Preview**.
- **Markdown supported · Formatting help** under the box opens a short cheat-sheet of the supported syntax.
- The counter next to it shows the length against the 5,000-character limit. It stays neutral while the box is empty, turns red with "at least 50 characters" for 1 to 49 characters, turns amber from 4,500 characters and red above 5,000. A line break counts as one character, exactly as the server counts it, and Markdown characters count too.
- Without JavaScript the tabs, help and counter are not shown and the plain text box works as usual.

| Supported                                                                    | Not supported                                                                       |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `**bold**`, `_italic_`                                                       | Code and code blocks (the text is kept, formatting removed); horizontal rules (removed) |
| Headings: `# Heading`, `## Sub-heading`, `### ...` (shown as small headings) | Images (removed entirely)                                                           |
| Bullet (`- item`) and numbered (`1. item`) lists                             | Tables and strikethrough (`~~text~~`) (shown as plain text)                         |
| Links written as `[label](https://example.com)` or `[label](mailto:...)`     | Bare URLs and `<https://...>` (shown as plain text, not turned into links)          |
| Blockquotes (`> quoted text`)                                                | Raw HTML (shown as literal text, never executed)                                    |
| Line breaks (a single newline starts a new line)                             | Links other than full `https://...`, `http://...` and `mailto:...` addresses, including relative links such as `/about` (the link is removed, the label kept) |

A few details:

- Headings are scaled down so they fit inside the page: `#` is the largest description heading, `##` the next size and `###` or more the smallest.
- Web links open in a new tab. `mailto:` links open your mail client instead, without a new tab. A link with no visible text (for example a badge whose image is removed) is dropped.
- To nest a list, indent the nested items with 4 spaces.
- A line that starts with `#` and a space becomes a heading (`#1` stays as written). A line that starts with `- ` becomes a list item, even directly under a line of text. A paragraph that starts with a number and a full stop (such as `1990. We started`) becomes a list numbered from that number. Put a backslash in front to keep the text as written: `\# Title`, `\- item`, `1990\. We started`.
- The preview shows a notice when content was removed: images, code, horizontal rules, lists nested more than nine levels deep (or quotes nested about twenty deep), or links that are not full `https://`, `http://` or `mailto:` addresses (including relative links).

---

## Your API Key

Each registration gets an API key with **write** scope. It lets you open and save the edit form, and use the [API](api-guide.md) for your own registration. The registry team can issue further keys on request, including **read-only** keys for tools that only need to view your registration.

Keep the key private: anyone who has it can change your registration.

---

## Editing an Existing Submission

1. Go to **/update/**.
2. Paste your API key into the text field and click **Retrieve My Registration**.
3. Your form will be pre-populated with all existing values.
4. Make your changes and click **Save Changes**. The description uses the same **Write** | **Preview** editor here too (see [Formatting the description](#formatting-the-description)).
5. Two emails are sent automatically:
   - **Administration office** — notified of your changes with a summary of what was updated.
   - **You** — a confirmation email is sent to your internal contact address listing exactly which fields changed (previous value → new value). If you saved without changing anything, you get no email.

Entering the key unlocks editing until you close the browser or for at most one hour (the default session length), so you can save several times without re-entering it.

If your service is **approved**, some changes send it back to review; see [Editing an approved service](#editing-an-approved-service).

> **The service name is fixed after submission.** On the edit form the **Name of the Service** field is greyed out and cannot be changed — every other field remains editable. If the name genuinely needs correcting, contact the de.NBI administration office; only they can change it.

!!! info "Security notice"
    If you receive a "your registration has been updated" email that you did not initiate, contact the de.NBI administration office immediately. The email will include instructions at the bottom.

---

## Browsing the Registry Catalogue

When the catalogue is enabled by the site administrators, a **Registry Catalogue** link appears in the top navigation bar.

The catalogue at **/catalogue/** lists all approved services and supports:

- **Search** — type in the search box to find services by service name, category, service centre, or responsible PI name — first name, last name, or the full "First Last" name (results update as you type, without a page reload)
- **Filter** — use the sidebar to filter by category or service centre. You can tick several in each list; a service is shown if it matches any ticked category and any ticked service centre
- **Sort** — by name (A→Z, Z→A), recently updated, or recently added
- **Group** — group results by category, service centre, or responsible PI (collapsible sections). A service with several PIs or categories appears under each of them.
- **Grid or list view** — the list view shows the full description
- **Pagination** — the page URL keeps your search, filters and view, so you can share or bookmark it

Each service card shows the service name, a short plain-text excerpt of the description, category, service centre, and optional EDAM topic badges. When Markdown descriptions are enabled, the list view shows the description with its formatting (links, emphasis, lists); cards show the same text without formatting. Where available, icon badges link directly to the service's GitHub repository, bio.tools entry, FAIRsharing record, or other registry.

The catalogue is read-only and requires no login.

---

## Field Help

### ELIXIR-DE registration (Section A)

Choose **Yes** to also register the service as an ELIXIR-DE service. This is only possible if you are affiliated with an ELIXIR-DE member institute.

### Institute / Affiliation (Section A)

The affiliation field is a searchable combobox. As you type, it filters a list of known
institute names drawn from registered PIs and previous submissions. If your institute is
not in the list, simply finish typing the full name and press **Enter** (or click
**Use "…"** in the dropdown) — the value you typed is accepted as-is. The **Host institute** field in Section C works the same way.

### Publications (PMIDs/DOIs)

Enter comma-separated PMIDs (PubMed IDs, digits only) or DOIs (starting with `10.`), at most 50:

```text
12345678, 10.1016/0022-2836(70)90057-4, 98765432
```

PMIDs are preferred and required for ELIXIR impact assessment. If your service is a consulting offering or has no publication yet, the form's help text asks you to enter `1234567`.

### Service Categories

Click any category to select or deselect it. Multiple selections are supported — no
modifier key needed. Selected categories appear as pills at the top of the field.

### Toolbox

If your service is part of a de.NBI toolbox, select **Yes** and enter the toolbox name.

### Responsible PIs, service centre and contacts (Section C)

- **Responsible PI(s):** pick one or more from the list. If your PI is not listed, select **Associated partner** and enter the PI's full name and affiliation in the note field that is then required.
- **Service centre:** pick exactly one.
- **Public contact:** an e-mail address or an `https://` support URL. It is meant to be shown publicly, for example on the de.NBI services page.
- **Internal contact:** name and e-mail address of the person the registry office should contact. You enter the e-mail address twice. It is never shown publicly or returned by the API, and it receives all e-mails about this registration.

### EDAM Ontology Annotations (Section B)

[EDAM](https://edamontology.org/) is a community ontology for bioinformatics. Annotating
your service makes it discoverable in bio.tools, the ELIXIR Tools & Data Services Registry,
and other platforms. These fields are **optional but strongly recommended**.

**EDAM Topics** describe the _scientific domain_ of your service. Examples:

- Proteomics
- Genomics
- Structural biology
- Metagenomics
- Metabolomics

**EDAM Operations** describe _what your service does computationally_. Examples:

- Sequence alignment
- Protein structure prediction
- Visualisation
- Pathway analysis
- Database search

**How to use the field:**

1. Click the search box and start typing — e.g. "prote" will filter to all terms containing that string.
2. Select a term from the dropdown. The term label and its EDAM accession (e.g. `topic_0121`) are shown.
3. You can select up to 6 terms per field.
4. To remove a term, click the × next to it.

**Tip:** If your tool is already registered in bio.tools, the form will offer to prefill EDAM
terms from your bio.tools entry automatically — see [bio.tools Prefill](#biotools-prefill) below.

### bio.tools Prefill {#biotools-prefill}

If your service already has a [bio.tools](https://bio.tools) entry, enter the URL in the
**Link to bio.tools entry** field (Section D) and move to the next field (press Tab).

The form looks up your tool in bio.tools and, if it is found, shows a banner **bio.tools data found.** with the list of available fields and two buttons, **Apply prefill** and **Dismiss**. Nothing is filled in until you click **Apply prefill**, which fills:

- Service name (if currently empty)
- Service description (if currently empty)
- Website URL (if currently empty)
- GitHub URL (if currently empty)
- Publications (if currently empty; at most 10 PMIDs or DOIs)
- License (if no license is selected yet and the bio.tools license matches an SPDX entry; otherwise the bio.tools license text goes into **License note**, if that is empty)
- EDAM Topics and EDAM Operations (adds the matching bio.tools terms; does not remove your existing selections)

**Important:** Prefilled data is a suggestion — always review and correct it before submitting.
Your submission is authoritative; bio.tools is the source of the prefill only.

If the lookup fails (tool not found, or bio.tools is temporarily unavailable), a
**bio.tools lookup failed:** banner is shown and you can fill in the fields manually.

### URL fields

All URLs must use **https://**. Plain http:// URLs are not accepted.

Domain-specific URL fields enforce a prefix match in addition to the HTTPS requirement:

| Field             | Required prefix            |
| ----------------- | -------------------------- |
| Website URL       | `https://` (any domain)    |
| Terms of use URL  | `https://` (any domain)    |
| GitHub repository | `https://github.com/`      |
| bio.tools entry   | `https://bio.tools/`       |
| FAIRsharing entry | `https://fairsharing.org/` |
| Other registry    | `https://` (any domain)    |

**FAIRsharing** (`https://fairsharing.org/`) is a curated resource of data standards, policies,
and databases. If your service or the data formats it uses are listed in FAIRsharing,
linking here helps with discoverability and FAIR compliance metadata.

### License

Select the license that governs how users may use your service. You can select one or more standard licenses from the list (e.g. MIT, Apache-2.0).

If none of the standard licenses apply, use the **License note** field below to enter:

- "Not applicable"
- "None of the above"
- "Proprietary"
- Or a custom license name

**Note:** At least one of License(s) or License note must be provided.

### Logo

You can optionally upload a logo for your service in Section B.

| Property         | Value                                            |
| ---------------- | ------------------------------------------------ |
| Accepted formats | PNG, JPEG, SVG                                   |
| PNG / JPEG       | Up to 10 MB and 25 megapixels (width × height)   |
| SVG              | Up to 1 MB                                       |
| Required         | No                                               |

These are the default limits; the form shows the limits configured for this
registry next to the upload field. The file is checked when you submit the form.

**What happens to uploaded logos:**

- The file type is detected from the file's content, not its name or extension
- JPEG and PNG files are re-encoded, which removes embedded metadata such as EXIF
- SVG files are reduced to standard drawing content: shapes, paths, text,
  gradients, patterns, clip paths, masks, filters and styles. Anything else
  (for example embedded HTML, animations, scripts or editor metadata) is
  removed. Links may only point inside the logo itself; style
  references may also use embedded `data:` resources such as fonts
- The original filename is not kept
- The logo appears in the admin view and in the API (`logo_url` field)

**Tips for SVG logos:** export a plain SVG (for example "Plain SVG" in Inkscape or
"SVG" in Illustrator/Figma) with fonts embedded rather than linked. An SVG is rejected with a message if:

- it is larger than the SVG limit;
- its root element is not `<svg>`, or it is nested unusually deeply;
- it contains a bitmap image (for example a PNG or JPEG saved "as SVG") or other content loaded from outside the file. Upload the bitmap itself as a PNG or JPEG instead, or use a vector-only SVG;
- its styles or attributes refer to resources in a form that is not supported.

The file may start with an XML declaration, a comment or a DOCTYPE before the `<svg>` element.

To replace a logo, upload a new one when editing your submission. Saving
an edit without choosing a new file keeps the current logo exactly as it is.

### Section F — Search tags, discoverability and outreach

| Field                         | What it means                                                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Keywords to identify usage without proper citation** | Names, abbreviations or URLs people use instead of a formal citation (for example in Materials and Methods). They help find uses of your service in publications. |
| **SEO-relevant keywords**     | Keywords for search engines and machine indexing; they complement a clear description.                         |
| **Survey participation**      | Whether you take part in de.NBI user surveys. This involves placing the de.NBI survey link on your service website; results are shared. Defaults to Yes. |
| **Comments**                  | Anything else the registry office should know.                                                                      |

### Section G — Consent

The single required checkbox covers both the data protection information and consent to de.NBI possibly featuring your service in outreach activities (social media, newsletters and similar). It must be ticked to submit or save the form, and it is never saved in a browser draft.

---

## Form Auto-Save / Draft

While filling out the form, your progress is automatically saved to your browser's **local storage**. If you navigate away or close the tab, you can return later and your partially completed form will still be there — a "Draft restored" banner appears at the top of the form when this happens. Click **Clear draft** in that banner to discard the saved values and start fresh.

This draft is a **browser-side convenience only** — it is not visible to the registry team and does not count as a submission until you click Submit.

A few things to know:

- **Submitting the form always clears the draft** — you will not see a restored draft after a successful submission.
- **Drafts expire after 7 days of inactivity** (the default; the registry can change this). If you return later without having submitted, the draft will be gone and the form will start blank.
- **Some fields are not saved** to the draft: the consent checkbox (you must tick it deliberately every time), the CAPTCHA, and any uploaded logo file.
- **If a service has been modified** by an administrator since you last visited the edit form, the old draft is automatically discarded. The form always shows the current registered values in that case.

---

## Submission Status Lifecycle

Your submission moves through these states:

| Status           | Meaning                                                                                                                                                                                                                            |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Submitted**    | Form submitted. The de.NBI administration office has been notified.                                                                                                                                                                |
| **Under Review** | Actively being reviewed by the service coordination team.                                                                                                                                                                          |
| **Approved**     | Your service has been accepted and will appear in the de.NBI services catalogue.                                                                                                                                                   |
| **Rejected**     | The submission was not accepted. You receive an email with the new status. Editing a rejected submission does not put it back into review; contact the administration office if you want it reviewed again.                      |
| **Deprecated**   | The service is no longer active. You can mark your own service as deprecated via the edit form (see below). Only the registry team can reverse this. Deprecated services are hidden from public listings but retain their history. |

A form that you have not submitted yet exists only as a [browser draft](#form-auto-save-draft); the registry does not see it.

### Editing an approved service

If you edit an **Approved** submission, what happens depends on which fields you changed. Changes to the core service information (for example the description, categories, responsible PIs or licenses) send the service back to **Submitted** for re-review, and its maturity tags are cleared until it is approved again. Changes to supplementary information (for example the logo, external links, EDAM annotations, keywords or contact details) keep it **Approved**. The registry decides which fields count as supplementary, so the exact list can change.

!!! tip "When in doubt, check your confirmation email"
    Every edit that changes something sends you an email listing exactly what changed. If the email includes the notice *"your service registration has been reset to Submitted"*, the re-review cycle has been triggered. If it does not, your service remains approved.

### What happens after submission?

1. The de.NBI Service Coordination Office reviews your submission.
2. You will receive an email at your internal contact address when the status changes.
3. If approved, your service will appear in the de.NBI services catalogue.
4. You can update your submission at any time using your API key.
5. Every time you change your submission, you will receive a confirmation email listing exactly what changed — so you always have a record of your updates.

### Deprecating Your Service

If your service is no longer active, you can mark it as deprecated directly from the edit form:

1. Log in with your API key via the Update / Edit page (`/update/`).
2. Scroll to the **Danger Zone** card in the right sidebar.
3. Click **Deprecate this service** and confirm in the dialog.

The status will be set to **Deprecated** and the registry team will be notified. Only registry admins can reverse a deprecation — contact [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de) if you need to reactivate a deprecated service.

---

## Lost Your API Key?

Contact the de.NBI administration office at [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de).
Include your service name and the email address you used as the internal contact.
An administrator will check the request and send you a new key.

---

## Troubleshooting the forms

- **A "Forbidden" (403) page after several attempts:** the forms limit how many submissions and edits one network address can make per hour. Wait and try again later.
- **CAPTCHA error on submit:** reload the page and tick the CAPTCHA checkbox again; a challenge expires after 10 minutes.

---

## Questions?

Email the de.NBI Service Coordination Office: [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de)
