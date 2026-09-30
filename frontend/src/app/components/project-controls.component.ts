import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiDatasetConfig, ApiDatasetConnection, ApiDatasetPreview, ApiDatasetRun, ApiDatasetSchedule, ApiDatasetScheduleConfig, ApiDatasetScheduleFrequency, ApiDatasetPublishMode, ApiService, Member, Project, ProjectVersion, errorMessage, schedulerErrorMessage } from '../services/api.service';
import { projectPath } from '../project-paths';

@Component({
  selector: 'app-project-controls',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    @if (canUseControls) {
      <div class="control-panel">
        @if (canUpload && section === 'versions') {
          <section class="control-section" aria-labelledby="upload-title">
            <div class="section-heading">
              <div>
                <h3 id="upload-title">Upload dashboard</h3>
                <p>Choose an HTML file or ZIP package. Add a CSV when the dashboard needs new data.</p>
              </div>
            </div>

            <div class="file-grid">
              <label class="file-field">
                <span>HTML or ZIP package <b aria-hidden="true">· required</b></span>
                <input
                  type="file"
                  accept=".html,.htm,.zip,text/html,application/zip,application/x-zip-compressed"
                  [disabled]="loading"
                  (change)="onPackageSelected($event)"
                />
              </label>
              <label class="file-field">
                <span>CSV data <b aria-hidden="true">· optional</b></span>
                <input
                  type="file"
                  accept=".csv,text/csv"
                  [disabled]="loading"
                  (change)="onUploadCsvSelected($event)"
                />
              </label>
            </div>
            @if (packageFile) {
              <p class="selection-note">Package: {{ packageFile.name }}</p>
            }
            @if (uploadCsvFile) {
              <p class="selection-note">CSV: {{ uploadCsvFile.name }}</p>
            }
            <div class="action-row">
              <button class="button primary" type="button" (click)="submitUpload()" [disabled]="!packageFile || loading">
                {{ loading ? 'Working…' : 'Upload draft' }}
              </button>
            </div>
          </section>
        }

        @if (section === 'versions') {
        <section class="control-section" aria-labelledby="versions-title">
          <div class="section-heading">
            <div>
              <h3 id="versions-title">Versions</h3>
              <p>Preview a draft before it goes live.</p>
            </div>
            <button class="button quiet" type="button" (click)="refresh.emit()" [disabled]="loading" aria-label="Refresh project versions">
              Refresh
            </button>
          </div>

          @if (versions.length > 0) {
            <ul class="version-list">
              @for (version of versions; track version.id) {
                <li class="version-row">
                  <div class="version-copy">
                    <div class="version-title-line">
                      <strong>Version {{ shortId(version.id) }}</strong>
                      @if (isLive(version)) {
                        <span class="state-chip live">Live</span>
                      } @else {
                        <span class="state-chip working">Working</span>
                      }
                    </div>
                    <span class="version-date">{{ formatDate(version.created_at) }}</span>
                    @if (version.csv_snapshot_id) {
                      <span class="csv-reference">CSV snapshot {{ shortId(version.csv_snapshot_id) }}</span>
                    }
                  </div>
                  <div class="version-actions">
                    <a class="text-link" [routerLink]="projectPath(project.owner_username, project.id)" [queryParams]="{version: version.id}">Preview</a>
                    @if (isOwnerOrAdmin && !isLive(version)) {
                      <button class="button small" type="button" (click)="publish.emit(version.id)" [disabled]="loading">
                        Publish
                      </button>
                    }
                  </div>
                </li>
              }
            </ul>
            @if (isOwnerOrAdmin && canRestorePrevious) {
              <div class="action-row restore-row">
                <button class="button" type="button" (click)="rollback.emit(project.id)" [disabled]="loading">
                  Restore previous
                </button>
              </div>
            }
          } @else {
            <p class="empty-state">No uploaded versions yet. Upload a package to start a draft.</p>
          }
        </section>

        }

        @if (canUpload && section === 'data') {
          <section class="control-section source-chooser" aria-labelledby="data-source-title">
            <div class="section-heading">
              <div>
                <h3 id="data-source-title">Choose a data source</h3>
                <p>Load a CSV file or connect an API, then save its data as a versioned snapshot.</p>
              </div>
            </div>
            <div class="source-options" role="group" aria-label="Data source type">
              <button type="button" class="source-option" [class.active]="selectedDataSource === 'csv'" [attr.aria-pressed]="selectedDataSource === 'csv'" (click)="selectDataSource('csv')">
                <span class="source-icon" aria-hidden="true">CSV</span><span><strong>CSV file</strong><small>Upload a file from your computer</small></span>
              </button>
              <button type="button" class="source-option" [class.active]="selectedDataSource === 'api'" [attr.aria-pressed]="selectedDataSource === 'api'" (click)="selectDataSource('api')">
                <span class="source-icon api-icon" aria-hidden="true">API</span><span><strong>API connection</strong><small>Fetch and preview data from an endpoint</small></span>
              </button>
            </div>
          </section>

          @if (selectedDataSource === 'csv') {
          <section class="control-section" aria-labelledby="replace-csv-title">
            <div class="section-heading">
              <div>
                <h3 id="replace-csv-title">Replace CSV data</h3>
                <p>Create a working version with new data, then preview and publish it.</p>
              </div>
            </div>
            <label class="file-field">
              <span>CSV file</span>
              <input type="file" accept=".csv,text/csv" [disabled]="loading" (change)="onReplacementCsvSelected($event)" />
            </label>
            @if (replacementCsvFile) {
              <div class="action-row">
                <span class="selection-note">{{ replacementCsvFile.name }}</span>
                <button class="button" type="button" (click)="replaceCsv.emit(replacementCsvFile)" [disabled]="loading">
                  Replace CSV
                </button>
              </div>
            }
          </section>
          } @else {
            <section class="control-section api-section" aria-labelledby="api-data-title">
              <div class="section-heading">
                <div>
                  <h3 id="api-data-title">Connect an API</h3>
                  <p>Save and test stores the connection and previews its response. Manual imports create a working version that you preview and publish from Versions. @if (apiScheduleEffectivePublishMode === 'auto_publish') { Scheduled refreshes publish changed data automatically; if the live dashboard changes during the fetch, the result waits for review. } @else { Scheduled refreshes create working versions that need review and publication. } Only a published version with an imported snapshot can read this CSV data.</p>
                </div>
                <button class="button" type="button" (click)="startNewApiConnection()" [disabled]="apiUnavailable">New connection</button>
              </div>

              @if (apiConnectionsLoading) {
                <p class="api-status" role="status">Loading saved connections…</p>
              }
              @if (apiError) {
                <p class="api-message error-message" role="alert">{{ apiError }}</p>
              }
              @if (apiNotice) {
                <p class="api-message success-message" role="status">{{ apiNotice }}</p>
              }

              <details id="api-connection-details" class="api-connection-details" [open]="apiConnectionDetailsOpen" (toggle)="onApiConnectionDetailsToggle($event)">
                <summary>Manage API connections and manual imports</summary>
                <div class="api-workspace">
                <aside class="saved-connections" aria-label="Saved API connections">
                  <h4>Saved connections</h4>
                  @if (apiConnections.length) {
                    <div class="connection-list">
                      @for (connection of apiConnections; track connection.id) {
                        <button type="button" class="connection-choice" [class.selected]="connection.id === editingApiConnectionId" (click)="editApiConnection(connection)" [disabled]="apiUnavailable">
                          <span class="connection-title"><strong>{{ connection.name }}</strong><span class="method-badge">{{ connection.method }}</span></span>
                          <small>{{ connection.url }}</small>
                          @if (connection.secret_headers.length) { <small>{{ connection.secret_headers.length }} saved credential{{ connection.secret_headers.length === 1 ? '' : 's' }}</small> }
                        </button>
                      }
                    </div>
                  } @else if (!apiConnectionsLoading) {
                    <p class="empty-state">No API connections yet. Add one to get started.</p>
                  }
                </aside>

                <div class="api-editor">
                  <form (submit)="saveApiConnection($event, false)">
                    <div class="api-form-heading">
                      <div><h4>{{ editingApiConnectionId ? 'Connection settings' : 'New API connection' }}</h4><p>Credential headers, URL query values, and request payloads are encrypted at rest. Saved secrets stay hidden in this editor.</p></div>
                      @if (editingApiConnectionId && deletingApiConnectionId !== editingApiConnectionId) { <div class="connection-form-actions"><button type="button" class="text-action" (click)="startNewApiConnection()" [disabled]="apiUnavailable">Clear form</button><button type="button" class="text-action danger-action" (click)="requestDeleteApiConnection()" [disabled]="apiUnavailable">Remove connection</button></div> }
                      @if (editingApiConnectionId && deletingApiConnectionId === editingApiConnectionId) { <div class="delete-confirm"><span>Remove this connection?</span><button type="button" class="text-action" (click)="cancelDeleteApiConnection()" [disabled]="apiUnavailable">Keep</button><button type="button" class="text-action danger-action" (click)="confirmDeleteApiConnection()" [disabled]="apiUnavailable">Remove</button></div> }
                    </div>
                    <div class="api-form-grid">
                      <label class="text-field"><span>Connection name</span><input type="text" autocomplete="off" [value]="apiNameDraft" (input)="setApiName($event)" placeholder="Sales API" [disabled]="apiUnavailable" /></label>
                      <label class="text-field"><span>Method</span><select [value]="apiMethodDraft" (change)="setApiMethod($event)" [disabled]="apiUnavailable"><option value="GET">GET</option><option value="POST">POST</option></select></label>
                      <label class="text-field api-url-field"><span>Endpoint URL</span><input type="url" autocomplete="off" [value]="apiUrlDraft" (input)="setApiUrl($event)" placeholder="https://api.example.com/v1/records" [disabled]="apiUnavailable" /><small>Use a public HTTPS endpoint that Agora can reach. Saved query values are hidden; leave blank query values unchanged or remove a query key to clear it.</small></label>
                    </div>

                    <div class="api-subheading"><div><strong>Request headers</strong><p>Add an Authorization, X-API-Key, or any custom header.</p></div><button type="button" class="text-action" (click)="addApiHeader()" [disabled]="apiUnavailable">+ Add header</button></div>
                    @if (apiHeaderDraft.length) {
                      <div class="header-list">
                        @for (header of apiHeaderDraft; track header.id) {
                          <div class="header-row">
                            <label class="text-field"><span>Header name</span><input type="text" [value]="header.name" (input)="updateHeaderName(header.id, $event)" placeholder="Authorization" [disabled]="apiUnavailable" /></label>
                            <label class="text-field"><span>Header value</span><input type="password" autocomplete="new-password" [value]="header.value" (input)="updateHeaderValue(header.id, $event)" [placeholder]="header.retained ? 'Saved secret · blank keeps it' : 'Bearer token or key'" [disabled]="apiUnavailable" /></label>
                            <button type="button" class="remove-header" [attr.aria-label]="'Remove ' + (header.name || 'header')" (click)="removeApiHeader(header.id)" [disabled]="apiUnavailable">×</button>
                            @if (header.retained) { <small class="retained-note">A saved secret is already stored. Leave this blank to keep it.</small> }
                          </div>
                        }
                      </div>
                    } @else {
                      <p class="field-note">No request headers added.</p>
                    }

                    @if (apiMethodDraft === 'POST') {
                      <label class="text-field body-field"><span>JSON payload <b>Optional</b></span><textarea rows="5" spellcheck="false" [value]="apiBodyDraft" (input)="setApiBody($event)" [placeholder]="apiBodySaved ? 'Saved payload · leave blank to keep it' : 'Enter a JSON object'" [disabled]="apiUnavailable"></textarea><small>Sent as the POST request body. Enter a JSON object. Saved payload contents are hidden.</small></label>
                      @if (apiBodySaved && !apiBodyDraft && !clearApiBody) { <button type="button" class="text-action clear-payload" (click)="clearSavedApiBody()" [disabled]="apiUnavailable">Remove saved payload</button> }
                      @if (clearApiBody) { <p class="field-note clear-payload">The saved payload will be removed when you save this connection.</p> }
                    }
                    <div class="api-form-grid mapping-fields">
                      <label class="text-field"><span>Records path <b>Optional</b></span><input type="text" [value]="apiRecordsPathDraft" (input)="setApiRecordsPath($event)" placeholder="data.items" [disabled]="apiUnavailable" /><small>Use a dotted path for nested results. Leave blank when the response is an array or has a top-level data array.</small></label>
                    </div>
                    <div class="api-actions">
                      <button class="button" type="submit" [disabled]="apiUnavailable || (editingApiConnectionId && !apiFormDirty)">{{ apiSaving ? 'Saving…' : (editingApiConnectionId && !apiFormDirty ? 'Saved' : 'Save connection') }}</button>
                      <button class="button primary" type="button" (click)="saveApiConnection($event, true)" [disabled]="apiUnavailable">{{ apiTesting ? 'Testing…' : 'Save and test' }}</button>
                    </div>
                  </form>

                  @if (apiPreview) {
                    <div class="api-preview" aria-live="polite">
                      <div class="preview-heading"><div><h4>API response preview</h4><p>{{ apiPreview.row_count }} row{{ apiPreview.row_count === 1 ? '' : 's' }} found · showing {{ apiPreview.rows.length }}</p></div><button type="button" class="text-action" (click)="clearApiPreview()">Dismiss</button></div>
                      @if (apiPreview.rows.length && apiPreview.columns.length) {
                        <div class="preview-table-wrap" tabindex="0" role="region" aria-label="API data preview">
                          <table><thead><tr>@for (column of apiPreview.columns; track column) { <th scope="col">{{ column }}</th> }</tr></thead><tbody>
                            @for (row of apiPreview.rows; track $index) { <tr>@for (column of apiPreview.columns; track column) { <td>{{ row[column] | json }}</td> }</tr> }
                          </tbody></table>
                        </div>
                      } @else {
                        <p class="empty-state">The API responded successfully but returned no rows.</p>
                      }
                    </div>
                  }

                  @if (selectedApiConnectionId) {
                    <div class="api-import">
                      <div><strong>Import to a working version</strong><p>Each import saves a snapshot and creates a working version. Preview it, then publish it from Versions to make this data available on the live dashboard.</p></div>
                      @if (versions.length) {
                        <div class="import-controls">
                          <label class="text-field"><span>Dashboard version</span><select (change)="apiImportBaseVersionId = inputValue($event)" [disabled]="apiUnavailable"><option value="" [selected]="!apiImportBaseVersionId">Choose a version</option>@for (version of versions; track version.id) { <option [value]="version.id" [selected]="apiImportBaseVersionId === version.id">Version {{ shortId(version.id) }}{{ version.is_working ? ' · Working' : (isLive(version) ? ' · Live' : '') }}</option> }</select></label>
                          <button type="button" class="button primary" (click)="importApiConnection()" [disabled]="apiUnavailable || !apiImportBaseVersionId || apiFormDirty || previewedApiConnectionId !== selectedApiConnectionId">{{ apiFormDirty ? 'Save changes before importing' : (previewedApiConnectionId !== selectedApiConnectionId ? 'Test connection before importing' : 'Import snapshot') }}</button>
                        </div>
                      } @else {
                        <p class="empty-state">Upload a dashboard version before importing API data.</p>
                      }
                    </div>

                    <details class="api-schedule-details">
                      <summary>Schedule details and run history</summary>
                    <section class="api-schedule" aria-labelledby="api-schedule-title">
                      <div class="api-schedule-heading">
                        <div><h4 id="api-schedule-title">Automatic refresh</h4><p>Choose when Agora calls this API. Changed data creates an immutable snapshot in a new version.</p></div>
                        <button type="button" class="text-action" (click)="loadApiDatasetRuns()" [disabled]="apiScheduleLoading || apiRunsLoading">Refresh history</button>
                      </div>
                      @if (apiScheduleLoading) { <p class="api-status" role="status">Loading schedule…</p> }
                      @if (apiScheduleError) { <p class="api-message error-message" role="alert">{{ apiScheduleError }}</p> }
                      @if (apiScheduleNotice) { <p class="api-message success-message" role="status">{{ apiScheduleNotice }}</p> }
                      @if (versions.length) {
                        <div class="api-schedule-form">
                          <label class="schedule-enabled"><input type="checkbox" [checked]="apiScheduleEnabledDraft" (change)="onScheduleEnabledChange($event)" [disabled]="apiScheduleBusy" /><span><strong>{{ apiScheduleEnabledDraft ? 'Schedule enabled' : 'Schedule paused' }}</strong><small>{{ apiScheduleEnabledDraft ? 'Agora will run this connection on the selected cadence.' : (apiScheduleEffectivePublishMode === 'auto_publish' ? 'Automatic-publishing approval is withdrawn while paused. Re-enable the schedule before running it.' : 'Scheduled runs are paused. You can still run a draft refresh manually.') }}</small></span></label>
                          <div class="schedule-fields">
                            <label class="text-field"><span>Repeat</span><select [value]="apiScheduleFrequencyDraft" (change)="onScheduleFrequencyChange($event)" [disabled]="apiScheduleBusy"><option value="hourly">Hourly</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="custom">Custom interval</option></select></label>
                            @if (apiScheduleFrequencyDraft === 'custom') {
                              <label class="text-field"><span>Run every (minutes)</span><input type="number" min="15" max="10080" step="15" [value]="apiScheduleIntervalDraft" (input)="onScheduleIntervalChange($event)" [disabled]="apiScheduleBusy" /><small>From 15 minutes to 7 days.</small></label>
                            } @else if (apiScheduleFrequencyDraft !== 'hourly') {
                              <label class="text-field"><span>Run at</span><input type="time" [value]="apiScheduleLocalTimeDraft" (input)="onScheduleLocalTimeChange($event)" [disabled]="apiScheduleBusy" /><small>Local time in {{ apiScheduleTimezoneDraft }}.</small></label>
                            } @else {
                              <div class="schedule-preset-note"><strong>Every hour</strong><small>Agora checks for changed data once an hour.</small></div>
                            }
                          </div>
                          <details class="schedule-advanced">
                            <summary>Advanced options</summary>
                            <div class="schedule-advanced-fields">
                              @if (apiScheduleFrequencyDraft === 'weekly') {
                                <fieldset class="schedule-weekdays"><legend>Run on</legend><label *ngFor="let day of weekdays"><input type="checkbox" [checked]="apiScheduleWeekdaysDraft.includes(day.value)" (change)="onScheduleWeekdayChange(day.value, $event)" [disabled]="apiScheduleBusy" /><span>{{ day.label }}</span></label></fieldset>
                              }
                              @if (apiScheduleFrequencyDraft === 'monthly') {
                                <label class="text-field"><span>Day of each month</span><select [value]="apiScheduleDayOfMonthDraft" (change)="onScheduleDayOfMonthChange($event)" [disabled]="apiScheduleBusy"><option *ngFor="let day of dayOfMonthOptions" [value]="day">{{ day }}</option></select><small>Choose days 1–28 so the schedule runs every month.</small></label>
                              }
                              <label class="text-field"><span>Time zone</span><input type="text" [value]="apiScheduleTimezoneDraft" (input)="onScheduleTimezoneChange($event)" placeholder="America/New_York" autocomplete="off" [disabled]="apiScheduleBusy" /><small>IANA time zone. Scheduled local times adjust for daylight saving changes.</small></label>
                              <label class="text-field"><span>Dashboard version</span><select [value]="apiScheduleBaseVersionIdDraft" (change)="onScheduleBaseVersionChange($event)" [disabled]="apiScheduleBusy"><option value="">Choose a version</option>@for (version of versions; track version.id) { <option [value]="version.id">Version {{ shortId(version.id) }}{{ version.is_working ? ' · Working' : (isLive(version) ? ' · Live' : '') }}</option> }</select><small>@if (apiScheduleEffectivePublishMode === 'auto_publish') { Used if no dashboard is currently published. } @else { Draft runs use this version as their dashboard base. }</small></label>
                            </div>
                          </details>
                          <fieldset class="schedule-publish-options">
                            <legend>When data changes</legend>
                            @if (isOwnerOrAdmin) {
                              <label class="publish-option"><input type="radio" name="api-refresh-publish-mode" value="draft" [checked]="apiSchedulePublishModeDraft === 'draft'" (change)="onSchedulePublishModeChange($event)" [disabled]="apiScheduleBusy" /><span><strong>Save a draft for review</strong><small>Creates a working version. Preview and publish it to update the live dashboard.</small></span></label>
                              <label class="publish-option"><input type="radio" name="api-refresh-publish-mode" value="auto_publish" [checked]="apiSchedulePublishModeDraft === 'auto_publish'" (change)="onSchedulePublishModeChange($event)" [disabled]="apiScheduleBusy" /><span><strong>Publish refreshed data automatically</strong><small>Each changed snapshot becomes live after its fetch finishes. If the live dashboard changed during the fetch, the result waits for review.</small></span></label>
                            } @else {
                              <div class="schedule-policy"><strong>{{ apiScheduleEffectivePublishMode === 'auto_publish' ? 'Publish refreshed data automatically' : 'Save a draft for review' }}</strong><small>Only a project owner can choose automatic publishing. Saving schedule changes as an editor switches to draft mode.</small></div>
                            }
                          </fieldset>
                          <div class="schedule-actions">
                            @if (apiSchedule) { <span class="schedule-next">@if (apiSchedule.enabled && apiSchedule.next_run_at) { Next run {{ formatDate(apiSchedule.next_run_at) }} } @else { No scheduled run pending }</span> }
                            <button type="button" class="button" (click)="saveApiDatasetSchedule()" [disabled]="apiScheduleBusy || !apiScheduleBaseVersionIdDraft">{{ apiScheduleSaving ? 'Saving…' : (apiSchedule ? 'Save schedule' : 'Create schedule') }}</button>
                            <button type="button" class="button primary" (click)="runApiDatasetNow()" [disabled]="apiScheduleBusy || !apiSchedule || apiScheduleDirty || (apiSchedule.publish_mode === 'auto_publish' && (!apiSchedule.enabled || !isOwnerOrAdmin))">{{ apiScheduleRunningNow ? 'Queuing…' : 'Run now' }}</button>
                            @if (apiSchedule && apiSchedule.publish_mode === 'auto_publish' && !apiSchedule.enabled) { <small class="schedule-policy-note">Re-enable this auto-publishing schedule before running it.</small> }
                          </div>
                          <p class="schedule-publication-note">@if (apiScheduleEffectivePublishMode === 'auto_publish') { Automatic publishing makes each changed snapshot live. Agora uses the currently published dashboard package and publishes only if the live version has not changed during the API fetch; otherwise the result waits for review. } @else { Scheduled imports create working versions from the selected dashboard version. Preview and publish a version from Versions before its data appears on the live dashboard. }</p>
                        </div>
                      } @else {
                        <p class="empty-state">Upload a dashboard version before scheduling API imports.</p>
                      }

                      <div class="api-run-history">
                        <div class="run-history-heading"><strong>Recent runs</strong><span *ngIf="apiSchedule?.last_run_at">Last run {{ formatDate(apiSchedule!.last_run_at!) }}</span></div>
                        @if (apiRunsLoading && !apiDatasetRuns.length) { <p class="api-status" role="status">Loading run history…</p> }
                        @if (apiRunsError) { <p class="api-message error-message" role="alert">{{ apiRunsError }}</p> }
                        @if (apiRunPollingPaused) { <p class="api-status" role="status">A run is still in progress. Refresh history to check again.</p> }
                        @if (apiDatasetRuns.length) {
                          <div class="run-list">
                            @for (run of apiDatasetRuns; track run.id) {
                              <article class="run-row">
                                <div class="run-copy"><span [class]="'run-status ' + run.status">{{ runStatusLabel(run.status) }}</span><small>{{ run.is_manual ? 'Manual' : 'Scheduled' }} · {{ formatDate(run.started_at || run.scheduled_for || run.created_at) }} · {{ run.attempt_count }} attempt{{ run.attempt_count === 1 ? '' : 's' }}</small>
                                  @if (run.version_id) { <small>Version {{ shortId(run.version_id) }}{{ run.snapshot_id ? ' · snapshot ' + shortId(run.snapshot_id) : '' }}{{ run.published ? ' · published' : ' · working version' }}</small> }
                                  @if (run.status === 'unchanged') { <small>The API response matches the latest snapshot. No new version was created.</small> }
                                  @if (run.status === 'needs_review') { <small class="run-error">This result was saved as a working version and needs review before it can affect the live dashboard.</small> }
                                  @if (run.error_message) { <small class="run-error">{{ run.error_message }} @if (run.error_code) { <span>({{ run.error_code }})</span> }</small> }
                                </div>
                                @if (run.finished_at) { <small class="run-finished">{{ formatDate(run.finished_at) }}</small> }
                              </article>
                            }
                          </div>
                        } @else if (!apiRunsLoading && !apiRunsError) {
                          <p class="empty-state">No runs yet. Save a schedule or run this connection now.</p>
                        }
                      </div>
                    </section>
                    </details>
                  }
                </div>
                </div>
              </details>
            </section>
          }
        }

        @if (isOwnerOrAdmin && section === 'access') {
          <section class="control-section" aria-labelledby="settings-title">
            <div class="section-heading">
              <div>
                <h3 id="settings-title">Project settings</h3>
                <p>Update the project details and dashboard write access.</p>
              </div>
            </div>
            <div class="settings-fields">
              <label class="text-field">
                <span>Project name</span>
                <input type="text" [value]="nameDraft" maxlength="120" (input)="onNameInput($event)" [disabled]="loading" />
              </label>
              <label class="text-field">
                <span>Description</span>
                <textarea rows="2" maxlength="1000" [value]="descriptionDraft" (input)="onDescriptionInput($event)" [disabled]="loading"></textarea>
              </label>
              <label class="check-field">
                <input type="checkbox" [checked]="allowViewerWritesDraft" (change)="onViewerWritesInput($event)" [disabled]="loading" />
                <span>Allow viewers to save dashboard records<small>Owners and editors can save records. CSV snapshots stay unchanged.</small></span>
              </label>
            </div>
            <div class="action-row">
              <button class="button" type="button" (click)="saveProjectChanges()" [disabled]="loading || !projectDirty">
                Save settings
              </button>
            </div>
          </section>

          <section class="control-section" aria-labelledby="sharing-title">
            <div class="section-heading">
              <div>
                <h3 id="sharing-title">People with access</h3>
                <p>Share with an existing Agora account by username.</p>
              </div>
            </div>
            <div class="member-add-row">
              <label class="text-field member-name-field">
                <span class="visually-hidden">Username</span>
                <input type="text" autocomplete="off" placeholder="Username" [value]="memberUsernameDraft" (input)="onMemberUsernameInput($event)" [disabled]="memberLoading || loading" />
              </label>
              <label class="text-field member-role-field">
                <span class="visually-hidden">Role</span>
                <select [value]="memberRoleDraft" (change)="onMemberRoleInput($event)" [disabled]="memberLoading || loading">
                  <option value="editor">Editor</option>
                  <option value="viewer">Viewer</option>
                </select>
              </label>
              <button class="button" type="button" (click)="addProjectMember()" [disabled]="memberLoading || loading || !memberUsernameDraft.trim()">
                Add
              </button>
            </div>
            @if (members.length > 0) {
              <ul class="member-list">
                @for (member of members; track member.account_id) {
                  <li>
                    <div class="member-copy">
                      <strong>{{ member.full_name || member.username }}</strong>
                      <span>{{ '@' + member.username }} · {{ member.role }}</span>
                    </div>
                    @if (member.role !== 'owner') {
                      <button class="button quiet small" type="button" (click)="removeMember.emit(member.username)" [disabled]="memberLoading || loading" [attr.aria-label]="'Remove ' + member.username">
                        Remove
                      </button>
                    }
                  </li>
                }
              </ul>
            } @else {
              <p class="empty-state">No additional members yet.</p>
            }
          </section>
        }
      </div>
    }
  `,
  styles: [`
    :host{display:block;color:var(--text)}
    .control-panel{display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);align-items:start;gap:24px}
    .control-section{min-width:0;padding:26px;border:1px solid var(--border);border-radius:12px;background:var(--surface)}
    .section-heading,.version-row,.member-list li,.action-row,.member-add-row{display:flex;align-items:center;justify-content:space-between;gap:14px}
    h3,p{margin-top:0}h3{margin-bottom:7px;font-size:1rem}.section-heading p,.empty-state{margin:0;color:var(--text-muted);font-size:.83rem;line-height:1.6}
    .section-heading{align-items:flex-start;margin-bottom:22px}.section-heading>div{min-width:0}
    .file-grid{display:grid;grid-template-columns:1fr;gap:18px}.file-field,.text-field{display:flex;min-width:0;flex-direction:column;gap:8px;color:var(--text);font-size:.8rem;font-weight:600}.file-field b{color:var(--text-muted);font-size:.72rem;font-weight:400}
    input[type="file"],input[type="text"],input[type="url"],input[type="password"],textarea,select{box-sizing:border-box;width:100%;min-width:0;border:1px solid var(--border-strong);border-radius:8px;color:var(--text);background:var(--bg);font:inherit}
    input[type="file"]{padding:10px;color:var(--text-muted);font-size:.75rem}input[type="file"]::file-selector-button{margin-right:8px;padding:7px 10px;border:0;border-radius:5px;color:var(--text);background:var(--surface-hover);cursor:pointer}
    input[type="text"],input[type="url"],input[type="password"],textarea,select{padding:11px 12px;font-size:.84rem;font-weight:400}textarea{resize:vertical}select option{color:var(--text);background:var(--surface)}
    input:focus-visible,textarea:focus-visible,select:focus-visible,button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
    .selection-note{margin:9px 0 0;color:var(--text-muted);font-size:.78rem;overflow-wrap:anywhere}.action-row{justify-content:flex-end;margin-top:20px}
    .button{display:inline-flex;min-height:36px;align-items:center;justify-content:center;padding:8px 12px;border:1px solid var(--border-strong);border-radius:8px;color:var(--text);background:var(--surface-raised);font:inherit;font-size:.8rem;font-weight:650;cursor:pointer;transition:background 120ms ease,border-color 120ms ease}.button:hover:not(:disabled){border-color:var(--accent);background:var(--surface-hover)}
    .button.primary{color:var(--primary-text);border-color:var(--primary-bg);background:var(--primary-bg)}.button.primary:hover:not(:disabled){background:var(--primary-hover)}.button.quiet{color:var(--text-muted);background:transparent}.button.small{min-height:30px;padding:6px 9px;font-size:.74rem}.button:disabled{opacity:.48;cursor:not-allowed}
    .version-list,.member-list{display:grid;gap:0;margin:0;padding:0;list-style:none}.version-row,.member-list li{align-items:center;padding:16px 0;border-bottom:1px solid var(--border)}.version-row:first-child,.member-list li:first-child{padding-top:0}.version-row:last-child,.member-list li:last-child{border-bottom:0;padding-bottom:0}
    .version-copy,.member-copy{display:grid;min-width:0;gap:6px}.version-title-line{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:var(--text);font-size:.84rem}.version-date,.csv-reference,.member-copy span{color:var(--text-muted);font-size:.76rem}.state-chip{padding:3px 7px;border-radius:999px;font-size:.63rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em}.state-chip.live{color:var(--success-text);background:var(--success-bg)}.state-chip.working{color:var(--accent-text);background:var(--accent-soft)}
    .version-actions{display:flex;flex:0 0 auto;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:12px}.text-link{color:var(--accent-text);font-size:.8rem;font-weight:650;text-decoration:none}.text-link:hover{text-decoration:underline}
    .settings-fields{display:grid;gap:18px}.check-field{display:flex;align-items:flex-start;gap:9px;color:var(--text-muted);font-size:.82rem;line-height:1.5;cursor:pointer}.check-field input{margin:2px 0 0;accent-color:var(--accent)}.check-field small{display:block;color:var(--text-muted);font-size:.72rem}.member-add-row{align-items:end;gap:8px}.member-name-field{flex:1 1 auto}.member-role-field{flex:0 0 100px}.member-list{margin-top:24px}.member-copy strong{color:var(--text);font-size:.84rem}
    .visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    .source-chooser,.api-section{grid-column:1/-1}.source-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.source-option{display:flex;align-items:center;gap:13px;min-width:0;padding:15px;border:1px solid var(--border-strong);border-radius:9px;background:var(--bg);color:var(--text);text-align:left;font:inherit;cursor:pointer}.source-option:hover{border-color:var(--accent)}.source-option.active{border-color:var(--accent);background:var(--accent-soft);box-shadow:0 0 0 1px var(--accent)}.source-option>span:last-child{display:grid;gap:4px}.source-option strong{font-size:.83rem}.source-option small,.connection-choice small,.field-note,.text-field small,.api-form-heading p,.api-subheading p,.preview-heading p,.api-import p{color:var(--text-muted);font-size:.73rem;line-height:1.45}.source-icon{display:grid;place-items:center;width:39px;height:39px;flex:0 0 auto;border-radius:9px;background:var(--surface-hover);color:var(--accent-text);font-size:.68rem;font-weight:800;letter-spacing:.03em}.api-icon{font-size:.62rem}.api-section{min-width:0}.api-workspace{display:grid;grid-template-columns:minmax(180px,.65fr) minmax(0,1.6fr);gap:26px}.saved-connections{min-width:0;padding-right:18px;border-right:1px solid var(--border)}.saved-connections h4,.api-form-heading h4,.preview-heading h4{margin:0 0 10px;font-size:.84rem}.connection-list{display:grid;gap:8px}.connection-choice{display:grid;gap:6px;width:100%;padding:11px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);text-align:left;cursor:pointer}.connection-choice:hover,.connection-choice.selected{border-color:var(--accent);background:var(--surface-hover)}.connection-choice:disabled{opacity:.6;cursor:wait}.connection-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.connection-title strong{font-size:.78rem;overflow-wrap:anywhere}.connection-choice small{overflow-wrap:anywhere}.method-badge{padding:2px 6px;border-radius:5px;background:var(--accent-soft);color:var(--accent-text);font-size:.6rem;font-weight:800}.api-editor{min-width:0}.api-form-heading,.api-subheading,.preview-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.api-form-heading p,.api-subheading p,.preview-heading p,.api-import p{margin:0}.api-form-heading{margin-bottom:17px}.api-form-heading h4{margin-bottom:4px}.api-form-grid{display:grid;grid-template-columns:minmax(0,1fr) 130px;gap:12px}.api-url-field{grid-column:1/-1}.api-subheading{align-items:center;margin:18px 0 10px}.api-subheading strong,.api-import strong{font-size:.8rem}.api-subheading p{margin-top:3px}.text-action{padding:4px;border:0;background:transparent;color:var(--accent-text);font:inherit;font-size:.74rem;font-weight:700;cursor:pointer}.text-action:disabled{opacity:.5;cursor:wait}.header-list{display:grid;gap:9px}.header-row{position:relative;display:grid;grid-template-columns:minmax(120px,.8fr) minmax(0,1.2fr) 30px;align-items:end;gap:9px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-raised)}.header-row .text-field{font-size:.72rem}.header-row .text-field input{padding:9px 10px}.remove-header{height:34px;border:1px solid var(--border);border-radius:7px;background:var(--bg);color:var(--text-muted);font-size:1.1rem;cursor:pointer}.remove-header:hover{border-color:var(--error-border);color:var(--error-text)}.retained-note{grid-column:2/3;color:var(--text-muted);font-size:.68rem}.field-note{margin:5px 0}.body-field{margin-top:15px}.body-field textarea{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.75rem}.body-field small{font-weight:400}.mapping-fields{grid-template-columns:minmax(0,1fr);margin-top:15px}.api-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:17px}.api-message{margin:0 0 13px;padding:10px 12px;border-radius:7px;font-size:.77rem;line-height:1.45}.error-message{color:var(--error-text);background:var(--error-bg)}.success-message{color:var(--success-text);background:var(--success-bg)}.api-status{margin:0 0 12px;color:var(--text-muted);font-size:.75rem}.api-preview{margin-top:20px;padding-top:17px;border-top:1px solid var(--border)}.preview-heading{margin-bottom:10px}.preview-heading h4{margin-bottom:4px}.preview-table-wrap{max-width:100%;overflow:auto;border:1px solid var(--border);border-radius:8px}.preview-table-wrap table{width:100%;min-width:440px;border-collapse:collapse;font-size:.73rem;text-align:left}.preview-table-wrap th,.preview-table-wrap td{max-width:270px;padding:9px 10px;border-bottom:1px solid var(--border);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.preview-table-wrap th{background:var(--surface-raised);color:var(--text-muted);font-size:.68rem}.preview-table-wrap tr:last-child td{border-bottom:0}.api-import{display:grid;gap:14px;margin-top:22px;padding:15px;border:1px solid var(--border);border-radius:9px;background:var(--surface-raised)}.import-controls{display:flex;align-items:end;gap:10px}.import-controls .text-field{flex:1}.import-controls select{padding:9px 10px}.api-import p{margin-top:4px}.text-field b{margin-left:4px;color:var(--text-muted);font-size:.68rem;font-weight:400}
    .connection-form-actions,.delete-confirm{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.danger-action{color:var(--error-text)}.delete-confirm{padding:7px;border:1px solid var(--error-border);border-radius:7px;color:var(--error-text);font-size:.71rem}.clear-payload{margin-top:8px;justify-self:start}
    .api-schedule{display:grid;gap:14px;margin-top:20px;padding:16px;border:1px solid var(--border);border-radius:9px;background:var(--bg)}.api-schedule-heading,.run-history-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.api-schedule-heading h4{margin:0 0 4px;font-size:.85rem}.api-schedule-heading p,.schedule-enabled small,.schedule-fields small,.schedule-policy small,.schedule-next,.schedule-publication-note,.run-history-heading span,.run-row small{color:var(--text-muted);font-size:.71rem;line-height:1.45}.api-schedule-form{display:grid;gap:12px}.schedule-enabled{display:flex;align-items:flex-start;gap:9px;padding:11px;border:1px solid var(--border);border-radius:8px;background:var(--surface)}.schedule-enabled input,.schedule-weekdays input{accent-color:var(--accent)}.schedule-enabled span{display:grid;gap:3px}.schedule-enabled strong,.schedule-policy strong{font-size:.75rem}.schedule-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.schedule-fields .text-field{display:grid;align-content:start;gap:5px;color:var(--text-muted);font-size:.73rem}.schedule-fields .text-field input,.schedule-fields .text-field select{min-width:0;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-strong);border-radius:7px;padding:9px 10px;font-size:.78rem}.schedule-weekdays{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:0;padding:8px 10px;border:1px solid var(--border);border-radius:7px}.schedule-weekdays legend{padding:0 4px;color:var(--text-muted);font-size:.7rem}.schedule-weekdays label{display:flex;align-items:center;gap:4px;font-size:.71rem}.schedule-policy{display:grid;align-content:start;gap:4px;padding:10px;border:1px solid var(--border);border-radius:7px;background:var(--surface)}.schedule-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:8px}.schedule-next{flex:1}.schedule-publication-note{margin:0;padding:9px 10px;border-radius:6px;background:var(--surface);line-height:1.5}.api-run-history{display:grid;gap:8px;padding-top:13px;border-top:1px solid var(--border)}.run-history-heading{align-items:center}.run-history-heading strong{font-size:.75rem}.run-list{display:grid;max-height:350px;overflow:auto;border:1px solid var(--border);border-radius:7px;background:var(--surface)}.run-row{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding:9px 10px;border-bottom:1px solid var(--border)}.run-row:last-child{border-bottom:0}.run-copy{display:grid;justify-items:start;gap:4px;min-width:0}.run-copy small{overflow-wrap:anywhere}.run-status{display:inline-flex;padding:2px 7px;border-radius:999px;background:var(--accent-soft);color:var(--accent-text);font-size:.62rem;font-weight:800}.run-status.succeeded,.run-status.unchanged{background:var(--success-bg);color:var(--success-text)}.run-status.failed,.run-status.needs_review{background:var(--error-bg);color:var(--error-text)}.run-error{color:var(--error-text)!important}.run-finished{flex:0 0 auto}
    @media(max-width:900px){.control-panel{grid-template-columns:1fr}.file-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.api-workspace{grid-template-columns:minmax(0,1fr)}}
    @media(max-width:620px){.control-section{padding:20px}.file-grid,.source-options{grid-template-columns:1fr}.version-row{align-items:flex-start;flex-direction:column}.version-actions{justify-content:flex-start}.member-add-row{align-items:stretch;flex-wrap:wrap}.member-role-field{flex:1 1 100px}.saved-connections{padding:0 0 17px;border-right:0;border-bottom:1px solid var(--border)}.api-form-grid,.header-row,.schedule-fields{grid-template-columns:1fr}.api-url-field{grid-column:auto}.header-row .remove-header{position:absolute;right:8px;top:8px;width:30px}.retained-note{grid-column:auto;padding-right:34px}.import-controls{align-items:stretch;flex-direction:column}.api-schedule{padding:12px}.api-schedule-heading{align-items:flex-start}.schedule-actions{align-items:stretch;flex-direction:column}.schedule-next{flex-basis:auto}.run-row{flex-direction:column}.run-finished{padding-left:4px}}
  `],
})
export class ProjectControlsComponent implements OnChanges, OnDestroy {
  readonly projectPath = projectPath;
  @Input({ required: true }) project!: Project;
  @Input({ required: true }) versions: ProjectVersion[] = [];
  @Input() section: 'versions' | 'access' | 'data' = 'versions';
  @Input() loading = false;
  @Input() members: Member[] = [];
  @Input() memberLoading = false;
  @Input() previousVersionId: string | null = null;

  @Output() readonly upload = new EventEmitter<{ file: File; csv: File | null }>();
  @Output() readonly publish = new EventEmitter<string>();
  @Output() readonly rollback = new EventEmitter<string>();
  @Output() readonly refresh = new EventEmitter<void>();
  @Output() readonly updateProject = new EventEmitter<{ name?: string; description?: string; allow_viewer_writes?: boolean }>();
  @Output() readonly addMember = new EventEmitter<{ username: string; role: 'editor' | 'viewer' }>();
  @Output() readonly removeMember = new EventEmitter<string>();
  @Output() readonly replaceCsv = new EventEmitter<File>();
  @Output() readonly importApiDataset = new EventEmitter<{ connectionId: string; baseVersionId: string }>();

  packageFile: File | null = null;
  uploadCsvFile: File | null = null;
  replacementCsvFile: File | null = null;
  nameDraft = '';
  descriptionDraft = '';
  allowViewerWritesDraft = false;
  memberUsernameDraft = '';
  memberRoleDraft: 'editor' | 'viewer' = 'editor';
  selectedDataSource: 'csv' | 'api' = 'csv';
  apiConnections: ApiDatasetConnection[] = [];
  apiConnectionsLoading = false;
  apiSaving = false;
  apiTesting = false;
  apiDeleting = false;
  apiError = '';
  apiNotice = '';
  apiPreview: ApiDatasetPreview | null = null;
  previewedApiConnectionId: string | null = null;
  editingApiConnectionId: string | null = null;
  selectedApiConnectionId: string | null = null;
  apiNameDraft = '';
  apiUrlDraft = '';
  apiMethodDraft: 'GET' | 'POST' = 'GET';
  apiBodyDraft = '';
  apiBodySaved = false;
  clearApiBody = false;
  apiRecordsPathDraft = '';
  apiImportBaseVersionId = '';
  apiFormDirty = false;
  deletingApiConnectionId: string | null = null;
  apiHeaderDraft: Array<{ id: number; name: string; value: string; retained: boolean; originalName?: string }> = [];
  readonly weekdays = [
    { value: 1, label: 'Mon' }, { value: 2, label: 'Tue' }, { value: 3, label: 'Wed' },
    { value: 4, label: 'Thu' }, { value: 5, label: 'Fri' }, { value: 6, label: 'Sat' },
    { value: 7, label: 'Sun' },
  ];
  apiSchedule: ApiDatasetSchedule | null = null;
  apiScheduleLoading = false;
  apiScheduleSaving = false;
  apiScheduleRunningNow = false;
  apiScheduleError = '';
  apiScheduleNotice = '';
  apiScheduleDirty = false;
  apiDatasetRuns: ApiDatasetRun[] = [];
  apiRunsLoading = false;
  apiRunsError = '';
  apiScheduleEnabledDraft = true;
  apiScheduleFrequencyDraft: 'hourly' | 'daily' | 'weekly' | 'monthly' | 'custom' = 'daily';
  readonly dayOfMonthOptions = Array.from({ length: 28 }, (_, index) => index + 1);
  apiScheduleIntervalDraft = '60';
  apiScheduleDayOfMonthDraft = '1';
  apiScheduleLocalTimeDraft = '09:00';
  apiScheduleWeekdaysDraft: number[] = [1];
  apiScheduleTimezoneDraft = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  apiScheduleBaseVersionIdDraft = '';
  apiSchedulePublishModeDraft: ApiDatasetPublishMode = 'draft';
  private apiConnectionsLoaded = false;
  private apiHeaderSequence = 0;
  private hasDataSourcePreference = false;
  apiConnectionDetailsOpen = false;
  private apiScheduleSelectionGeneration = 0;
  private apiRunsRequestGeneration = 0;
  private apiRunsPollTimer: ReturnType<typeof setTimeout> | null = null;
  private apiRunsPollCount = 0;
  apiRunPollingPaused = false;

  constructor(private readonly api: ApiService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['project'] && this.project) {
      this.nameDraft = this.project.name;
      this.descriptionDraft = this.project.description ?? '';
      this.allowViewerWritesDraft = this.project.allow_viewer_writes;
      const dataSourcePreference = this.readDataSourcePreference(this.project.id);
      this.selectedDataSource = dataSourcePreference ?? 'csv';
      this.hasDataSourcePreference = dataSourcePreference !== null;
      this.apiConnections = [];
      this.apiConnectionsLoaded = false;
      this.resetApiSchedule();
    }
    if (changes['versions']) this.chooseDefaultImportVersion();
    if (changes['section'] && this.section === 'data') this.chooseDefaultImportVersion();
    if (this.section === 'data' && (this.selectedDataSource === 'api' || !this.hasDataSourcePreference)) this.ensureApiConnectionsLoaded();
  }

  get isOwnerOrAdmin(): boolean {
    return this.project?.role === 'owner' || this.project?.role === 'admin';
  }

  get canUpload(): boolean {
    return this.isOwnerOrAdmin || this.project?.role === 'editor';
  }

  get canUseControls(): boolean {
    return this.isOwnerOrAdmin || this.canUpload;
  }

  get canRestorePrevious(): boolean {
    return !!this.previousVersionId;
  }

  get projectDirty(): boolean {
    return (Boolean(this.nameDraft.trim()) && this.nameDraft.trim() !== this.project?.name)
      || this.descriptionDraft !== (this.project?.description ?? '')
      || this.allowViewerWritesDraft !== this.project?.allow_viewer_writes;
  }

  onPackageSelected(event: Event): void {
    this.packageFile = this.selectedFile(event);
  }

  ngOnDestroy(): void {
    this.apiScheduleSelectionGeneration++;
    this.apiRunsRequestGeneration++;
    this.clearApiRunsPoll();
  }

  get selectedApiConnection(): ApiDatasetConnection | null {
    return this.apiConnections.find((connection) => connection.id === this.selectedApiConnectionId) ?? null;
  }

  selectDataSource(source: 'csv' | 'api'): void {
    this.selectedDataSource = source;
    this.hasDataSourcePreference = true;
    if (this.project) {
      try {
        globalThis.localStorage?.setItem(this.dataSourcePreferenceKey(this.project.id), source);
      } catch {
        // Source preference is only a convenience; keep the selector usable when storage is unavailable.
      }
    }
    if (source === 'api') this.ensureApiConnectionsLoaded();
  }

  private readDataSourcePreference(projectId: string): 'csv' | 'api' | null {
    try {
      const preference = globalThis.localStorage?.getItem(this.dataSourcePreferenceKey(projectId));
      return preference === 'api' || preference === 'csv' ? preference : null;
    } catch {
      return null;
    }
  }

  private dataSourcePreferenceKey(projectId: string): string {
    return `agora:project:${projectId}:data-source`;
  }

  get apiUnavailable(): boolean {
    return this.loading || this.apiSaving || this.apiTesting || this.apiDeleting;
  }

  get apiScheduleBusy(): boolean {
    return this.apiUnavailable || this.apiScheduleLoading || this.apiScheduleSaving || this.apiScheduleRunningNow;
  }

  get apiScheduleEffectivePublishMode(): ApiDatasetPublishMode {
    return this.isOwnerOrAdmin || this.apiScheduleDirty
      ? this.apiSchedulePublishModeDraft
      : (this.apiSchedule?.publish_mode ?? this.apiSchedulePublishModeDraft);
  }

  inputValue(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value;
  }

  ensureApiConnectionsLoaded(): void {
    if (!this.project || this.apiConnectionsLoaded || this.apiConnectionsLoading) return;
    this.apiConnectionsLoading = true;
    this.apiError = '';
    this.api.apiDatasets(this.project.id).subscribe({
      next: (result) => {
        this.apiConnections = result.connections;
        this.apiConnectionsLoaded = true;
        this.apiConnectionsLoading = false;
        if (!result.connections.length) this.apiConnectionDetailsOpen = true;
        if (result.connections.length) {
          this.editApiConnection(result.connections[0]);
          if (!this.hasDataSourcePreference) this.selectDataSource('api');
        }
        else this.startNewApiConnection();
      },
      error: (error) => {
        this.apiConnectionsLoading = false;
        this.apiError = errorMessage(error);
      },
    });
  }

  startNewApiConnection(): void {
    if (this.apiUnavailable) return;
    this.apiConnectionDetailsOpen = true;
    this.editingApiConnectionId = null;
    this.selectedApiConnectionId = null;
    this.apiNameDraft = '';
    this.apiUrlDraft = '';
    this.apiMethodDraft = 'GET';
    this.apiBodyDraft = '';
    this.apiBodySaved = false;
    this.clearApiBody = false;
    this.apiRecordsPathDraft = '';
    this.apiHeaderDraft = [];
    this.apiPreview = null;
    this.previewedApiConnectionId = null;
    this.apiFormDirty = false;
    this.deletingApiConnectionId = null;
    this.apiError = '';
    this.apiNotice = '';
    this.resetApiSchedule();
  }

  onApiConnectionDetailsToggle(event: Event): void {
    this.apiConnectionDetailsOpen = (event.target as HTMLDetailsElement).open;
  }

  editApiConnection(connection: ApiDatasetConnection): void {
    this.editingApiConnectionId = connection.id;
    this.selectedApiConnectionId = connection.id;
    this.apiNameDraft = connection.name;
    this.apiUrlDraft = connection.url;
    this.apiMethodDraft = connection.method;
    this.apiBodyDraft = connection.body ? JSON.stringify(connection.body, null, 2) : '';
    this.apiBodySaved = connection.body_saved;
    this.clearApiBody = false;
    this.apiRecordsPathDraft = connection.records_path ?? '';
    const secrets = new Set(connection.secret_headers ?? []);
    this.apiHeaderDraft = Object.entries(connection.headers ?? {}).map(([name, value]) => ({
      id: ++this.apiHeaderSequence,
      name,
      value: secrets.has(name) ? '' : value,
      retained: secrets.has(name),
      originalName: name,
    }));
    this.apiPreview = null;
    this.previewedApiConnectionId = null;
    this.apiFormDirty = false;
    this.apiError = '';
    this.apiNotice = '';
    this.deletingApiConnectionId = null;
    this.chooseDefaultImportVersion();
    this.loadApiDatasetSchedule(connection.id);
  }

  private resetApiSchedule(): void {
    this.apiScheduleSelectionGeneration++;
    this.apiRunsRequestGeneration++;
    this.clearApiRunsPoll();
    this.apiSchedule = null;
    this.apiScheduleLoading = false;
    this.apiScheduleSaving = false;
    this.apiScheduleRunningNow = false;
    this.apiScheduleError = '';
    this.apiScheduleNotice = '';
    this.apiScheduleDirty = false;
    this.apiDatasetRuns = [];
    this.apiRunsLoading = false;
    this.apiRunsError = '';
    this.apiRunsPollCount = 0;
    this.apiRunPollingPaused = false;
    this.apiScheduleEnabledDraft = true;
    this.apiScheduleFrequencyDraft = 'daily';
    this.apiScheduleIntervalDraft = '60';
    this.apiScheduleDayOfMonthDraft = '1';
    this.apiScheduleLocalTimeDraft = '09:00';
    this.apiScheduleWeekdaysDraft = [1];
    this.apiScheduleTimezoneDraft = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    this.apiScheduleBaseVersionIdDraft = this.defaultApiScheduleBaseVersion();
    this.apiSchedulePublishModeDraft = 'draft';
  }

  private loadApiDatasetSchedule(connectionId: string): void {
    this.resetApiSchedule();
    const generation = this.apiScheduleSelectionGeneration;
    this.apiScheduleLoading = true;
    this.api.apiDatasetSchedule(this.project.id, connectionId).subscribe({
      next: ({ schedule }) => {
        if (generation !== this.apiScheduleSelectionGeneration || this.selectedApiConnectionId !== connectionId) return;
        this.apiSchedule = schedule;
        this.apiScheduleEnabledDraft = schedule?.enabled ?? true;
        this.apiScheduleFrequencyDraft = schedule?.frequency === 'interval'
          ? (schedule.interval_minutes === 60 ? 'hourly' : 'custom')
          : (schedule?.frequency ?? 'daily');
        this.apiScheduleIntervalDraft = String(schedule?.interval_minutes ?? 60);
        this.apiScheduleDayOfMonthDraft = String(schedule?.day_of_month ?? 1);
        this.apiScheduleLocalTimeDraft = schedule?.local_time ?? '09:00';
        this.apiScheduleWeekdaysDraft = schedule?.weekdays?.length ? [...schedule.weekdays] : [1];
        this.apiScheduleTimezoneDraft = schedule?.timezone || (Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC');
        this.apiScheduleBaseVersionIdDraft = schedule?.base_version_id || this.defaultApiScheduleBaseVersion();
        this.apiSchedulePublishModeDraft = this.isOwnerOrAdmin ? (schedule?.publish_mode ?? 'draft') : 'draft';
        this.apiScheduleDirty = false;
        this.apiScheduleLoading = false;
      },
      error: (error) => {
        if (generation !== this.apiScheduleSelectionGeneration) return;
        this.apiScheduleLoading = false;
        this.apiScheduleError = schedulerErrorMessage(error);
      },
    });
    this.loadApiDatasetRuns(connectionId, true);
  }

  loadApiDatasetRuns(connectionId: string | null = this.selectedApiConnectionId, silent = false): void {
    if (!connectionId || !this.project) return;
    this.clearApiRunsPoll();
    if (!silent) {
      this.apiRunsPollCount = 0;
      this.apiRunPollingPaused = false;
    }
    const selectionGeneration = this.apiScheduleSelectionGeneration;
    const requestGeneration = ++this.apiRunsRequestGeneration;
    if (!silent) this.apiRunsLoading = true;
    this.apiRunsError = '';
    this.api.apiDatasetRuns(this.project.id, connectionId).subscribe({
      next: ({ runs }) => {
        if (selectionGeneration !== this.apiScheduleSelectionGeneration || requestGeneration !== this.apiRunsRequestGeneration) return;
        this.apiDatasetRuns = runs;
        this.apiRunsLoading = false;
        if (runs.some((run) => run.status === 'queued' || run.status === 'running') && this.apiRunsPollCount < 12) {
          this.apiRunsPollCount++;
          this.apiRunsPollTimer = setTimeout(() => {
            if (selectionGeneration === this.apiScheduleSelectionGeneration && connectionId === this.selectedApiConnectionId) {
              this.loadApiDatasetRuns(connectionId, true);
            }
          }, 5000);
        } else if (runs.some((run) => run.status === 'queued' || run.status === 'running')) {
          this.apiRunPollingPaused = true;
        } else {
          this.apiRunsPollCount = 0;
          this.apiRunPollingPaused = false;
        }
      },
      error: (error) => {
        if (selectionGeneration !== this.apiScheduleSelectionGeneration || requestGeneration !== this.apiRunsRequestGeneration) return;
        this.apiRunsLoading = false;
        this.apiRunsError = schedulerErrorMessage(error);
      },
    });
  }

  private clearApiRunsPoll(): void {
    if (this.apiRunsPollTimer !== null) {
      clearTimeout(this.apiRunsPollTimer);
      this.apiRunsPollTimer = null;
    }
  }

  private defaultApiScheduleBaseVersion(): string {
    return this.versions.find((version) => version.is_working)?.id
      ?? this.versions.find((version) => this.isLive(version))?.id
      ?? this.versions[0]?.id
      ?? '';
  }

  private markApiScheduleChanged(): void {
    this.apiScheduleDirty = true;
    this.apiScheduleError = '';
    this.apiScheduleNotice = '';
  }

  onScheduleEnabledChange(event: Event): void {
    this.apiScheduleEnabledDraft = (event.target as HTMLInputElement).checked;
    this.markApiScheduleChanged();
  }

  onScheduleFrequencyChange(event: Event): void {
    const value = this.inputValue(event);
    if (value === 'hourly' || value === 'daily' || value === 'weekly' || value === 'monthly' || value === 'custom') {
      this.apiScheduleFrequencyDraft = value;
    }
    if (value === 'hourly') this.apiScheduleIntervalDraft = '60';
    this.markApiScheduleChanged();
  }

  onScheduleIntervalChange(event: Event): void {
    this.apiScheduleIntervalDraft = this.inputValue(event);
    this.markApiScheduleChanged();
  }

  onScheduleLocalTimeChange(event: Event): void {
    this.apiScheduleLocalTimeDraft = this.inputValue(event);
    this.markApiScheduleChanged();
  }

  onScheduleDayOfMonthChange(event: Event): void {
    this.apiScheduleDayOfMonthDraft = this.inputValue(event);
    this.markApiScheduleChanged();
  }

  onScheduleWeekdayChange(day: number, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    const selected = new Set(this.apiScheduleWeekdaysDraft);
    if (checked) selected.add(day);
    else selected.delete(day);
    this.apiScheduleWeekdaysDraft = [...selected].sort((a, b) => a - b);
    this.markApiScheduleChanged();
  }

  onScheduleTimezoneChange(event: Event): void {
    this.apiScheduleTimezoneDraft = this.inputValue(event).trim();
    this.markApiScheduleChanged();
  }

  onScheduleBaseVersionChange(event: Event): void {
    this.apiScheduleBaseVersionIdDraft = this.inputValue(event);
    this.markApiScheduleChanged();
  }

  onSchedulePublishModeChange(event: Event): void {
    this.apiSchedulePublishModeDraft = this.inputValue(event) === 'auto_publish' ? 'auto_publish' : 'draft';
    this.markApiScheduleChanged();
  }

  saveApiDatasetSchedule(): void {
    const connectionId = this.selectedApiConnectionId;
    if (!connectionId || !this.project || this.apiScheduleBusy) return;
    const config = this.readApiScheduleConfig();
    if (!config) return;
    this.apiScheduleSaving = true;
    this.apiScheduleError = '';
    this.apiScheduleNotice = '';
    this.api.saveApiDatasetSchedule(this.project.id, connectionId, config).subscribe({
      next: ({ schedule }) => {
        if (connectionId !== this.selectedApiConnectionId) return;
        this.apiScheduleSaving = false;
        this.apiSchedule = schedule;
        this.apiScheduleEnabledDraft = schedule.enabled;
        this.apiScheduleFrequencyDraft = schedule.frequency === 'interval'
          ? (schedule.interval_minutes === 60 ? 'hourly' : 'custom')
          : schedule.frequency;
        this.apiScheduleIntervalDraft = String(schedule.interval_minutes ?? 60);
        this.apiScheduleDayOfMonthDraft = String(schedule.day_of_month ?? 1);
        this.apiScheduleLocalTimeDraft = schedule.local_time ?? '09:00';
        this.apiScheduleWeekdaysDraft = schedule.weekdays?.length ? [...schedule.weekdays] : [1];
        this.apiScheduleTimezoneDraft = schedule.timezone;
        this.apiScheduleBaseVersionIdDraft = schedule.base_version_id;
        this.apiSchedulePublishModeDraft = schedule.publish_mode;
        this.apiScheduleDirty = false;
        this.apiScheduleNotice = schedule.enabled
          ? 'Schedule saved and enabled. New data will be saved as a version according to the selected publish mode.'
          : 'Schedule saved and paused. You can still run it manually.';
        this.loadApiDatasetRuns(connectionId, true);
      },
      error: (error) => {
        this.apiScheduleSaving = false;
        this.apiScheduleError = schedulerErrorMessage(error);
      },
    });
  }

  private readApiScheduleConfig(): ApiDatasetScheduleConfig | null {
    if (!this.apiScheduleBaseVersionIdDraft || !this.versions.some((version) => version.id === this.apiScheduleBaseVersionIdDraft)) {
      this.apiScheduleError = 'Choose a dashboard version for scheduled imports.';
      return null;
    }
    const timezone = this.apiScheduleTimezoneDraft.trim();
    if (!timezone) {
      this.apiScheduleError = 'Enter an IANA time zone, such as America/New_York.';
      return null;
    }
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format();
    } catch {
      this.apiScheduleError = 'Enter a valid IANA time zone, such as America/New_York.';
      return null;
    }

    const backendFrequency: ApiDatasetScheduleFrequency = this.apiScheduleFrequencyDraft === 'hourly' || this.apiScheduleFrequencyDraft === 'custom'
      ? 'interval'
      : this.apiScheduleFrequencyDraft;
    const config: ApiDatasetScheduleConfig = {
      enabled: this.apiScheduleEnabledDraft,
      frequency: backendFrequency,
      timezone,
      base_version_id: this.apiScheduleBaseVersionIdDraft,
      publish_mode: this.isOwnerOrAdmin ? this.apiSchedulePublishModeDraft : 'draft',
      day_of_month: null,
    };
    if (this.apiScheduleFrequencyDraft === 'hourly' || this.apiScheduleFrequencyDraft === 'custom') {
      const minutes = this.apiScheduleFrequencyDraft === 'hourly' ? 60 : Number(this.apiScheduleIntervalDraft);
      if (!Number.isSafeInteger(minutes) || minutes < 15 || minutes > 10080) {
        this.apiScheduleError = 'Choose an interval from 15 to 10,080 whole minutes.';
        return null;
      }
      config.interval_minutes = minutes;
    } else {
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(this.apiScheduleLocalTimeDraft)) {
        this.apiScheduleError = 'Choose a valid local run time.';
        return null;
      }
      config.local_time = this.apiScheduleLocalTimeDraft;
    }
    if (this.apiScheduleFrequencyDraft === 'weekly') {
      if (!this.apiScheduleWeekdaysDraft.length) {
        this.apiScheduleError = 'Choose at least one day for a weekly schedule.';
        return null;
      }
      config.weekdays = [...this.apiScheduleWeekdaysDraft].sort((a, b) => a - b);
    } else if (this.apiScheduleFrequencyDraft === 'monthly') {
      const day = Number(this.apiScheduleDayOfMonthDraft);
      if (!Number.isSafeInteger(day) || day < 1 || day > 28) {
        this.apiScheduleError = 'Choose a day from 1 to 28 so the schedule can run every month.';
        return null;
      }
      config.day_of_month = day;
    }
    return config;
  }

  runApiDatasetNow(): void {
    const connectionId = this.selectedApiConnectionId;
    if (!connectionId || !this.project || !this.apiSchedule || this.apiScheduleDirty || this.apiScheduleBusy) return;
    if (this.apiSchedule.publish_mode === 'auto_publish' && (!this.apiSchedule.enabled || !this.isOwnerOrAdmin)) return;
    this.apiScheduleRunningNow = true;
    this.apiScheduleError = '';
    this.apiScheduleNotice = '';
    this.apiRunsPollCount = 0;
    this.apiRunPollingPaused = false;
    this.api.runApiDatasetNow(this.project.id, connectionId).subscribe({
      next: ({ run }) => {
        if (connectionId !== this.selectedApiConnectionId) return;
        this.apiScheduleRunningNow = false;
        this.apiScheduleNotice = 'Run queued. Its status will update here; draft runs create a working version that must be published from Versions.';
        this.apiDatasetRuns = [run, ...this.apiDatasetRuns.filter((item) => item.id !== run.id)].slice(0, 20);
        this.loadApiDatasetRuns(connectionId, true);
      },
      error: (error) => {
        this.apiScheduleRunningNow = false;
        this.apiScheduleError = schedulerErrorMessage(error);
      },
    });
  }

  runStatusLabel(status: ApiDatasetRun['status']): string {
    switch (status) {
      case 'queued': return 'Queued';
      case 'running': return 'Running';
      case 'succeeded': return 'Succeeded';
      case 'unchanged': return 'No change';
      case 'needs_review': return 'Needs review';
      case 'failed': return 'Failed';
      case 'cancelled': return 'Cancelled';
    }
  }

  addApiHeader(): void {
    this.apiHeaderDraft = [...this.apiHeaderDraft, { id: ++this.apiHeaderSequence, name: '', value: '', retained: false }];
    this.markApiFormChanged();
  }

  updateHeaderName(id: number, event: Event): void {
    const value = this.inputValue(event);
    this.apiHeaderDraft = this.apiHeaderDraft.map((header) => header.id === id
      ? { ...header, name: value, retained: header.retained && value.trim().toLowerCase() === header.originalName?.toLowerCase() }
      : header);
    this.markApiFormChanged();
  }

  updateHeaderValue(id: number, event: Event): void {
    const value = this.inputValue(event);
    this.apiHeaderDraft = this.apiHeaderDraft.map((header) => header.id === id ? { ...header, value } : header);
    this.markApiFormChanged();
  }

  removeApiHeader(id: number): void {
    this.apiHeaderDraft = this.apiHeaderDraft.filter((header) => header.id !== id);
    this.markApiFormChanged();
  }

  setApiName(event: Event): void { this.apiNameDraft = this.inputValue(event); this.markApiFormChanged(); }
  setApiUrl(event: Event): void { this.apiUrlDraft = this.inputValue(event); this.markApiFormChanged(); }
  setApiMethod(event: Event): void { this.apiMethodDraft = this.inputValue(event) === 'POST' ? 'POST' : 'GET'; this.markApiFormChanged(); }
  setApiBody(event: Event): void { this.apiBodyDraft = this.inputValue(event); this.clearApiBody = false; this.markApiFormChanged(); }
  setApiRecordsPath(event: Event): void { this.apiRecordsPathDraft = this.inputValue(event); this.markApiFormChanged(); }
  clearSavedApiBody(): void { this.clearApiBody = true; this.apiBodyDraft = ''; this.markApiFormChanged(); }

  private markApiFormChanged(): void {
    this.apiFormDirty = true;
    this.apiPreview = null;
    this.previewedApiConnectionId = null;
    this.apiNotice = '';
  }

  clearApiPreview(): void {
    this.apiPreview = null;
    this.previewedApiConnectionId = null;
  }

  saveApiConnection(event: Event, testAfterSave: boolean): void {
    event.preventDefault();
    if (this.apiUnavailable || !this.project) return;
    if (this.editingApiConnectionId && !this.apiFormDirty) {
      const connectionId = this.editingApiConnectionId;
      this.apiError = '';
      if (testAfterSave) {
        this.apiNotice = 'Testing the saved connection…';
        this.runApiTest(connectionId);
      } else {
        this.apiNotice = this.previewedApiConnectionId === connectionId
          ? 'Connection already saved. Its tested preview is ready to import.'
          : 'Connection already saved. Test it before importing API data.';
      }
      return;
    }
    const config = this.readApiConfig();
    if (!config) return;
    this.apiSaving = true;
    this.apiError = '';
    this.apiNotice = '';
    const save = this.editingApiConnectionId
      ? this.api.updateApiDataset(this.project.id, this.editingApiConnectionId, config)
      : this.api.createApiDataset(this.project.id, config);
    save.subscribe({
      next: ({ connection }) => {
        this.apiSaving = false;
        this.apiConnectionsLoaded = true;
        this.apiConnections = [connection, ...this.apiConnections.filter((item) => item.id !== connection.id)];
        this.editApiConnection(connection);
        this.apiNotice = testAfterSave
          ? 'Connection saved. Testing the endpoint…'
          : 'Connection saved. Test it, then import a snapshot and publish its version from Versions to make the data available on the live dashboard.';
        if (testAfterSave) this.runApiTest(connection.id);
      },
      error: (error) => {
        this.apiSaving = false;
        this.apiError = errorMessage(error);
      },
    });
  }

  importApiConnection(): void {
    if (!this.apiUnavailable && !this.apiFormDirty && this.selectedApiConnectionId && this.apiImportBaseVersionId && this.previewedApiConnectionId === this.selectedApiConnectionId) {
      this.importApiDataset.emit({ connectionId: this.selectedApiConnectionId, baseVersionId: this.apiImportBaseVersionId });
    }
  }

  private runApiTest(connectionId: string): void {
    if (!this.project) return;
    this.apiTesting = true;
    this.api.testApiDataset(this.project.id, connectionId).subscribe({
      next: (preview) => {
        this.apiTesting = false;
        this.apiPreview = preview;
        this.previewedApiConnectionId = connectionId;
        this.apiNotice = 'Connection saved and tested. Import a snapshot below, then publish its version from Versions to make the data available on the live dashboard.';
        this.selectedApiConnectionId = connectionId;
      },
      error: (error) => {
        this.apiTesting = false;
        this.apiPreview = null;
        this.previewedApiConnectionId = null;
        this.apiError = errorMessage(error);
      },
    });
  }

  private readApiConfig(): ApiDatasetConfig | null {
    const name = this.apiNameDraft.trim();
    const url = this.apiUrlDraft.trim();
    if (!name) { this.apiError = 'Enter a name for this connection.'; return null; }
    let parsedUrl: URL;
    try { parsedUrl = new URL(url); } catch { this.apiError = 'Enter a valid API URL.'; return null; }
    if (parsedUrl.protocol !== 'https:') { this.apiError = 'The API URL must use HTTPS.'; return null; }

    const headers: Record<string, string | null> = {};
    const seen = new Set<string>();
    for (const header of this.apiHeaderDraft) {
      const headerName = header.name.trim();
      if (!headerName) {
        if (header.value.trim()) { this.apiError = 'Add a name for each header value.'; return null; }
        continue;
      }
      const key = headerName.toLowerCase();
      if (seen.has(key)) { this.apiError = `The ${headerName} header is listed more than once.`; return null; }
      seen.add(key);
      if (!header.value && header.retained) headers[headerName] = '';
      else if (header.value) headers[headerName] = header.value;
    }

    let body: Record<string, unknown> | null = null;
    if (this.apiMethodDraft === 'POST' && this.apiBodyDraft.trim()) {
      let parsedBody: unknown;
      try { parsedBody = JSON.parse(this.apiBodyDraft); } catch { this.apiError = 'Enter a valid JSON payload.'; return null; }
      if (!parsedBody || typeof parsedBody !== 'object' || Array.isArray(parsedBody)) { this.apiError = 'The JSON payload must be an object.'; return null; }
      body = parsedBody as Record<string, unknown>;
    }
    return {
      name,
      url,
      method: this.apiMethodDraft,
      headers,
      body,
      clear_body: this.clearApiBody || undefined,
      records_path: this.apiRecordsPathDraft.trim() || null,
    };
  }

  requestDeleteApiConnection(): void {
    if (this.editingApiConnectionId && !this.apiUnavailable) this.deletingApiConnectionId = this.editingApiConnectionId;
  }

  cancelDeleteApiConnection(): void { this.deletingApiConnectionId = null; }

  confirmDeleteApiConnection(): void {
    const connectionId = this.deletingApiConnectionId;
    if (!connectionId || !this.project || this.apiUnavailable) return;
    this.apiDeleting = true;
    this.apiError = '';
    this.api.deleteApiDataset(this.project.id, connectionId).subscribe({
      next: () => {
        this.apiDeleting = false;
        this.apiConnections = this.apiConnections.filter((connection) => connection.id !== connectionId);
        this.apiNotice = 'Connection removed. Existing imported snapshots remain available.';
        this.startNewApiConnection();
        this.apiNotice = 'Connection removed. Existing imported snapshots remain available.';
      },
      error: (error) => {
        this.apiDeleting = false;
        this.apiError = errorMessage(error);
      },
    });
  }

  private chooseDefaultImportVersion(): void {
    if (!this.versions.length) {
      this.apiImportBaseVersionId = '';
      this.apiScheduleBaseVersionIdDraft = '';
      return;
    }
    const preferred = this.defaultApiScheduleBaseVersion();
    if (!this.apiImportBaseVersionId || !this.versions.some((version) => version.id === this.apiImportBaseVersionId)) {
      this.apiImportBaseVersionId = preferred;
    }
    if (!this.apiScheduleBaseVersionIdDraft || !this.versions.some((version) => version.id === this.apiScheduleBaseVersionIdDraft)) {
      const savedBase = this.apiSchedule?.base_version_id;
      this.apiScheduleBaseVersionIdDraft = savedBase && this.versions.some((version) => version.id === savedBase) ? savedBase : preferred;
    }
  }

  onUploadCsvSelected(event: Event): void {
    this.uploadCsvFile = this.selectedFile(event);
  }

  onReplacementCsvSelected(event: Event): void {
    this.replacementCsvFile = this.selectedFile(event);
  }

  submitUpload(): void {
    if (this.packageFile) {
      this.upload.emit({ file: this.packageFile, csv: this.uploadCsvFile });
    }
  }

  onNameInput(event: Event): void {
    this.nameDraft = (event.target as HTMLInputElement).value;
  }

  onDescriptionInput(event: Event): void {
    this.descriptionDraft = (event.target as HTMLTextAreaElement).value;
  }

  onViewerWritesInput(event: Event): void {
    this.allowViewerWritesDraft = (event.target as HTMLInputElement).checked;
  }

  saveProjectChanges(): void {
    if (!this.project) return;

    const changes: { name?: string; description?: string; allow_viewer_writes?: boolean } = {};
    const name = this.nameDraft.trim();
    if (name && name !== this.project.name) changes.name = name;
    if (this.descriptionDraft !== (this.project.description ?? '')) changes.description = this.descriptionDraft;
    if (this.allowViewerWritesDraft !== this.project.allow_viewer_writes) {
      changes.allow_viewer_writes = this.allowViewerWritesDraft;
    }
    if (Object.keys(changes).length > 0) this.updateProject.emit(changes);
  }

  onMemberUsernameInput(event: Event): void {
    this.memberUsernameDraft = (event.target as HTMLInputElement).value;
  }

  onMemberRoleInput(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.memberRoleDraft = value === 'viewer' ? 'viewer' : 'editor';
  }

  addProjectMember(): void {
    const username = this.memberUsernameDraft.trim();
    if (username) this.addMember.emit({ username, role: this.memberRoleDraft });
  }

  isLive(version: ProjectVersion): boolean {
    return this.project?.published_version_id === version.id || Boolean(version.is_published);
  }

  shortId(id: string): string {
    return id.slice(0, 8);
  }

  formatDate(timestamp: string): string {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime())
      ? timestamp
      : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  private selectedFile(event: Event): File | null {
    return (event.target as HTMLInputElement).files?.item(0) ?? null;
  }
}
