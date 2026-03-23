---
icon: material/account
---

# User Guide — de.NBI Service Registration

## What is this?

This is the de.NBI & ELIXIR-DE Service Registration system. Use it to register your
bioinformatics service for inclusion in the [de.NBI services catalogue](https://www.denbi.de/services).

---

## Registering a New Service

1. Go to **/register/** and fill in the form. All fields marked **(\*)** are required.

2. Work through each section:

   | Section              | What you need                                                                  |
   | -------------------- | ------------------------------------------------------------------------------ |
   | A — General          | Today's date, your name and affiliation                                        |
   | B — Service Data     | Service name, description, year, category, EDAM annotations, publications      |
   | C — Responsibilities | Responsible PI(s), host institute, contact emails                              |
   | D — Links            | Service website, terms of use, license, optional repository and registry links |
   | E — KPIs             | Whether KPI monitoring is in place                                             |
   | F — Discoverability  | Keywords for search and citation tracking                                      |
   | G — Consent          | Data protection consent (required to submit)                                   |

3. Click **Submit Registration**.

4. **Save your API key immediately.** On the confirmation page you will see a box containing your unique API key. This key:
   - Is shown **exactly once** — it will not be emailed to you.
   - Is required to edit your submission later.
   - If lost, contact [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de) to have a new one issued.

---

## Editing an Existing Submission

1. Go to **/update/**.
2. Paste your API key into the text field and click **Retrieve My Registration**.
3. Your form will be pre-populated with all existing values.
4. Make your changes and click **Save Changes**.
5. The de.NBI administration office will be notified automatically.

> **Note:** If your submission was already approved and you edit it, the status will reset to "Submitted" for re-review.

---

## Field Help

### Publications (PMIDs/DOIs)

Enter comma-separated PMIDs (PubMed IDs, digits only) or DOIs (starting with `10.`):

```text
12345678, 10.1016/0022-2836(70)90057-4, 98765432
```

PMIDs are required for ELIXIR impact assessment.

### Service Categories

Select all that apply by checking the relevant boxes. Multiple selections are supported.

### Toolbox

If your service is part of a de.NBI toolbox, select **Yes** and enter the toolbox name.
If you select "Associated partner" as a PI, you must provide their name and affiliation.

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

The form will automatically look up your tool in bio.tools and, if found, show a banner:

> **Metadata found in bio.tools** — Fields have been pre-filled from your bio.tools entry.
> Review and adjust before saving.

Click **Apply prefill** to populate the following fields from bio.tools data:

- Service name (if currently empty)
- Service description (if currently empty)
- Website URL (if currently empty)
- GitHub URL (if currently empty)
- Publications (if currently empty)
- License (if a matching license is available)
- EDAM Topics (adds bio.tools annotations; does not remove your existing selections)
- EDAM Operations (same)

**Important:** Prefilled data is a suggestion — always review and correct it before submitting.
Your submission is authoritative; bio.tools is the source of the prefill only.

If the bio.tools lookup fails (tool not found, or bio.tools is temporarily unavailable),
a warning banner is shown and you can fill in the fields manually.

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

### Logo Upload (Section B)

You can optionally upload a logo for your service. This field is not required.

| Property | Value |
|---|---|
| Accepted formats | PNG, JPEG, SVG |
| Maximum size | 10 MB |
| Required | No |

**How to upload:**

1. In Section B, click the logo upload field
2. Select a PNG, JPEG, or SVG file from your computer
3. The logo is validated automatically on form submission

**What happens to uploaded logos:**

- JPEG and PNG files are re-encoded to strip any embedded EXIF metadata
- SVG files are sanitised to remove scripts, event handlers, and external links
- The original filename is discarded — a unique identifier is assigned internally
- The logo appears in the admin view and is accessible via the API (`logo_url` field)

To replace a logo, simply upload a new one when editing your submission. Previous logos are retained on disk but replaced for display purposes.

---

### Outreach and Survey fields (Section F)

| Field                    | What it means                                                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Outreach consent**     | You agree to de.NBI potentially showcasing your service on social media or newsletters. You can withdraw this consent at any time by editing your submission. |
| **Survey participation** | You are willing to be contacted for de.NBI user surveys (e.g. annual usage statistics). Defaults to Yes.                                                      |

### License

Select the license that governs how users may use your service.
Select "Not applicable" for services without a software license (e.g. pure databases).

---

## Submission Status Lifecycle

Your submission moves through these states:

| Status           | Meaning                                                                                               |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| **Draft**        | Saved locally but not yet submitted. Draft submissions older than 30 days are automatically deleted.  |
| **Submitted**    | Form submitted. The de.NBI administration office has been notified.                                   |
| **Under Review** | Actively being reviewed by the service coordination team.                                             |
| **Approved**     | Your service has been accepted and will appear in the de.NBI services catalogue.                      |
| **Rejected**     | The submission was not accepted. You will receive an email explaining why. You may edit and resubmit. |
| **Deprecated**   | The service is no longer active. You can mark your own service as deprecated via the edit form (see below). Only the registry team can reverse this. |

If you edit a submission that was already **Approved**, the status resets to **Submitted** and the administration office is notified to re-review your changes.

### Deprecating Your Service

If your service is no longer active, you can mark it as deprecated directly from the edit form:

1. Log in with your API key via the Update / Edit page (`/update/`).
2. Scroll to the **Danger Zone** card in the right sidebar.
3. Click **Deprecate this service** and confirm in the dialog.

The status will be set to **Deprecated** and the registry team will be notified. Only registry admins can reverse a deprecation — contact [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de) if you need to reactivate a deprecated service.

---

## What happens after submission?

1. The de.NBI Service Coordination Office reviews your submission.
2. You will receive an email at your internal contact address when the status changes.
3. If approved, your service will appear in the de.NBI services catalogue.
4. You can update your submission at any time using your API key.

---

## Lost Your API Key?

Contact the de.NBI administration office at [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de).
Include your service name and the email address you used as the internal contact.
An administrator will verify your identity and issue a new key.

---

## Questions?

Email the de.NBI Service Coordination Office: [servicecoordination@denbi.de](mailto:servicecoordination@denbi.de)
