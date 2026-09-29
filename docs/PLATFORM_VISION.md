# Agora platform vision

## The idea

Give people a simple, governed place to host and share HTML dashboards and presentations they create with AI agents or other tools. The HTML is the thing people see. Its JavaScript should run in the viewer's browser and use project data supplied by the platform.

The platform is not an HTML editor. People create or change their files elsewhere, then upload a new project version. Later, an automated deployment path could replace manual uploads.

This is a product direction and discovery note, not a build specification. The proposed technical shape below is a starting recommendation, not a final architecture decision.

## The experience we are aiming for

1. A person creates an account with a username, full name, and password, then signs in.
2. They land on **My Space**, with projects they own and projects shared with them.
3. They create a project and upload an HTML entry file with any companion JavaScript, CSS, images, or other assets it needs.
4. They open the project and see the hosted HTML rendered, with its JavaScript running in the browser. Initially the HTML can be independent of data; CSV and API-fed data can follow.
5. Owners and editors get small, clear controls to upload a new version, manage access, and work with publication. Viewers get the project without controls they cannot use.
6. People can view the same published project from different computers. Redeploying the platform does not erase project files or data.

The interface should feel calm, polished, and professional: a restrained dark-blue palette, clear hierarchy, compact login and controls, and no oversized forms or decorative clutter.

## The product shape

- **A project is an HTML-led package.** The HTML file is its entry point. A project may include JavaScript, CSS, images, and other companion files. CSV and API data can be added as the data experience grows.
- **The platform hosts and governs files; it does not author HTML.** For the first version, people upload files produced outside the platform. AI-agent or other automated deployment can come later.
- **Working and published versions are useful, but the workflow should stay light.** A simple draft/preview and publish step can protect the version viewers rely on without introducing a complex release-management system.
- **Access should be easy to explain.** The starting roles are project owner, project editor, and viewer, plus one platform-wide administrator role.
- **Uploaded JavaScript is part of the dashboard experience.** It should be able to render the dashboard and, when the data capability exists, receive data through the platform. Browser code should not contain database credentials or call Oracle directly.

## A possible progression

This is a working sequence, not a commitment to defer features.

1. **First usable version:** account and sign-in, My Space, create a project, upload HTML and companion files, render it reliably for viewers, and preserve uploaded versions across redeployments.
2. **CSV data:** let an owner or editor upload a CSV. Replacing the current CSV should be the default; keep prior versions so a project can be understood or restored. Allow a new HTML-and-CSV package to be uploaded together.
3. **Connected data:** let dashboard code request data through a project-scoped platform API. Agora's backend uses a read-only Starburst connector for approved external database reads.
4. **Saved dashboard changes:** if a dashboard lets a viewer edit data, save those changes through a platform API into durable application-owned storage. Define the data and permissions first; this differs from replacing the source CSV.

## A practical storage and runtime direction

These are recommendations to explore because the product needs to survive pod loss, support many projects, and allow JavaScript while keeping project owners in control.

- **Do not create one Oracle table per project.** Keep project, membership, version, publication, and audit metadata in shared tables keyed by project ID. A table per project creates ongoing schema, migration, and operations work as projects multiply.
- **Keep files out of the application pod's writable filesystem.** Store each uploaded project package and CSV as a durable, immutable version. Oracle LOBs are a reasonable simple starting point for modest-sized packages if database capacity and backup practices support them; durable object storage is another option if it is already available and governed. Keep a project/version pointer to the current draft and published package.
- **Separate package versions from live data.** Replacing a CSV creates a new data snapshot and moves the current pointer; it does not overwrite the previous snapshot. If a dashboard needs user-edited records, store them separately from the source CSV, in shared project-scoped records with defined keys and schema/version metadata. Do not turn arbitrary dashboard data into project-specific physical tables.
- **Keep the Python service stateless across deployments.** Any replica should be able to serve a request using durable Oracle/storage state. Immutable published assets can be cached and served to many viewers; request-specific user state belongs in the browser or durable backend, not local pod memory or disk.
- **Run uploaded HTML in an isolated browser frame and origin.** Allow the JavaScript needed for dashboards, but isolate it from the platform's authenticated application and provide data through a narrow, permission-checked API. “Support general HTML and JavaScript” should still leave explicit limits around network access, popups, navigation, downloads, and server-side code. Uploaded browser scripts should not get Oracle or connector secrets.
- **Treat viewing concurrency and write concurrency separately.** Many people viewing an immutable published dashboard is a normal web-serving problem. When people write shared data, save through backend transactions and use record/version checks so a stale edit cannot silently overwrite a newer one. Real-time collaboration can wait unless users need to see edits appear immediately.

This direction uses Oracle's LOB support for durable file values where that fits the deployment, browser sandboxing guidance for script isolation, and HTTP conditional requests as a standard way to detect overlapping updates. See [Oracle LOB storage](https://docs.oracle.com/en/database/oracle/oracle-database/19/adlob/using-oracle-LOBs-storage.html), [MDN iframe sandbox guidance](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe), and [MDN conditional requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Conditional_requests).

## Product and technical direction

- **Frontend:** Angular.
- **Backend:** Python.
- **Database:** Oracle, accessed by the Agora backend through `python-oracledb`.
- **Environment behavior:** The backend selects DEV or PROD from its environment configuration; the application should not ask users to choose “local” versus “deployed.”
- **External data:** The Agora backend uses Trino's Python client to query approved Starburst sources through a project-scoped API.

## Questions to resolve through discovery

The following remain open; the answers can be staged rather than decided all at once.

### How much is in one project package?

Is one HTML entry page enough for the first version, or should navigation among multiple HTML pages work too? Should an upload be a ZIP/package with relative paths, or should the platform accept individual files?

### What does the first CSV experience do?

Should CSVs only be replaced, or is appending rows important early? What file size and row counts are realistic? Should old CSV versions be kept indefinitely or for a defined period?

### What can a dashboard write?

Is the write use case editing rows in a known dataset, saving form submissions, or keeping arbitrary project-specific state? Who may write, who may see the result, and can users undo or audit changes? A small first version may support read-only dashboards until one write use case is clear.

### Who can publish?

Can an editor publish their own changes, or does publishing belong to the owner or platform administrator? The simplest workflow may be one draft plus one published version, with no extra approval roles.

### What should uploaded JavaScript be allowed to do?

Which capabilities are needed for useful dashboards: fetching platform data, accessing external URLs, forms, popups, file downloads, or navigation? Which capabilities should be blocked by default? How should these rules apply when a project is opened directly rather than inside the platform?

### What account and access rules are needed?

How will users recover a password, and can project editors invite other users? What can the platform-wide administrator do, and should admin actions be recorded?

### How should file storage be operated?

What package and CSV size limits are acceptable? Does the deployed Oracle environment support the required LOB capacity and backup/restore workflow, or is governed object storage available? This determines whether package bytes belong in Oracle or in a separate durable store; neither should be the pod filesystem.

## A starting hypothesis to validate

Keep the first workflow small: create an account, open My Space, create a project, upload an HTML-led package, preview it with browser JavaScript enabled inside an isolation boundary, and publish it for viewers. Keep project metadata and access in shared Oracle tables; keep package versions in durable storage; defer API connections and dashboard write-back until their first concrete data use case is defined. The initial manual upload path can later be automated without changing what a project means.
