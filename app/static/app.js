// =============================================================================
// OpenFMR Admin UI — Client‑side JavaScript
// =============================================================================
// This file runs on the Conflict Resolution page (resolution.html).
// It:
//   1. Parses the embedded conflict JSON data.
//   2. Uses the jsdiff library to compute a line‑by‑line diff.
//   3. Renders colour‑coded diff output into the unified diff panel.
//   4. Wires up the three resolution buttons to POST decisions to the API.
// =============================================================================

"use strict";

document.addEventListener("DOMContentLoaded", function () {
    // -----------------------------------------------------------------
    // 1. Parse embedded conflict data from the inline <script> block
    // -----------------------------------------------------------------
    const dataElement = document.getElementById("conflict-data");
    if (!dataElement) {
        // Not on the resolution page — nothing to do.
        return;
    }

    let conflictData;
    try {
        conflictData = JSON.parse(dataElement.textContent);
    } catch (err) {
        console.error("Failed to parse conflict data:", err);
        return;
    }

    const { module, conflictId, localStateJson, incomingMasterJson } = conflictData;

    // -----------------------------------------------------------------
    // 2. Compute the diff with jsdiff and render it
    // -----------------------------------------------------------------
    const diffOutput = document.getElementById("diff-output");

    if (typeof Diff !== "undefined" && Diff.diffLines) {
        renderDiff(localStateJson, incomingMasterJson, diffOutput);
    } else {
        diffOutput.textContent = "[jsdiff library not loaded — diff unavailable]";
        console.warn("jsdiff (Diff) is not available on the page.");
    }

    // -----------------------------------------------------------------
    // 3. Wire up resolution buttons
    // -----------------------------------------------------------------
    const btnKeepLocal    = document.getElementById("btn-keep-local");
    const btnAcceptMaster = document.getElementById("btn-accept-master");
    const btnMerge        = document.getElementById("btn-merge");
    const btnSubmitMerge  = document.getElementById("btn-submit-merge");
    const mergeSection    = document.getElementById("merge-editor-section");
    const mergeEditor     = document.getElementById("merge-editor");
    const statusAlert     = document.getElementById("status-alert");

    // --- Keep Local ---
    if (btnKeepLocal) {
        btnKeepLocal.addEventListener("click", function () {
            submitResolution(module, conflictId, "keep_local", null);
        });
    }

    // --- Accept Incoming Master ---
    if (btnAcceptMaster) {
        btnAcceptMaster.addEventListener("click", function () {
            submitResolution(module, conflictId, "accept_master", null);
        });
    }

    // --- Merge Manually (show editor) ---
    if (btnMerge && mergeSection && mergeEditor) {
        btnMerge.addEventListener("click", function () {
            // Pre‑populate the merge editor with the local state so the
            // steward has a starting point to edit.
            mergeEditor.value = localStateJson;
            mergeSection.classList.remove("d-none");
            mergeEditor.focus();
        });
    }

    // --- Submit Merge ---
    if (btnSubmitMerge && mergeEditor) {
        btnSubmitMerge.addEventListener("click", function () {
            let mergedResource;
            try {
                mergedResource = JSON.parse(mergeEditor.value);
            } catch (err) {
                showStatus("danger", "Invalid JSON in the merge editor. Please fix syntax errors and try again.");
                return;
            }
            submitResolution(module, conflictId, "merge", mergedResource);
        });
    }

    // =================================================================
    // Helper — render colour‑coded diff
    // =================================================================
    /**
     * Compute a line‑level diff between two JSON strings and render the
     * result as colour‑coded HTML inside the given container element.
     *
     * @param {string} oldText  — the "local state" JSON text
     * @param {string} newText  — the "incoming master" JSON text
     * @param {HTMLElement} container — the DOM element to render into
     */
    function renderDiff(oldText, newText, container) {
        // Compute changes
        const changes = Diff.diffLines(oldText, newText);

        // Build HTML from the diff parts
        const fragment = document.createDocumentFragment();

        changes.forEach(function (part) {
            const span = document.createElement("span");

            if (part.added) {
                span.className = "diff-added";
                span.textContent = part.value;
            } else if (part.removed) {
                span.className = "diff-removed";
                span.textContent = part.value;
            } else {
                span.className = "diff-equal";
                span.textContent = part.value;
            }

            fragment.appendChild(span);
        });

        // Clear any existing content and insert the diff
        container.textContent = "";
        container.appendChild(fragment);
    }

    // =================================================================
    // Helper — submit resolution decision via fetch()
    // =================================================================
    /**
     * POST the steward's decision to the /resolve endpoint.
     *
     * @param {string} mod            — "cr" or "hfr"
     * @param {string} id             — the conflict UUID
     * @param {string} decision       — "keep_local" | "accept_master" | "merge"
     * @param {object|null} merged    — merged resource (only for "merge")
     */
    async function submitResolution(mod, id, decision, merged) {
        // Disable all buttons to prevent double‑submission
        setButtonsDisabled(true);
        showStatus("info", '<i class="bi bi-hourglass-split"></i> Submitting resolution…');

        const payload = {
            decision: decision,
        };
        if (merged !== null) {
            payload.merged_resource = merged;
        }

        try {
            const response = await fetch("/resolve/" + mod + "/" + id, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const result = await response.json();

            if (response.ok) {
                showStatus(
                    "success",
                    '<i class="bi bi-check-circle-fill"></i> ' +
                    (result.message || "Conflict resolved successfully.") +
                    ' <a href="/" class="alert-link">Return to Dashboard</a>'
                );
            } else {
                showStatus(
                    "danger",
                    '<i class="bi bi-x-circle-fill"></i> Error: ' +
                    (result.detail || "Unknown server error.")
                );
                setButtonsDisabled(false);
            }
        } catch (err) {
            console.error("Network error:", err);
            showStatus(
                "danger",
                '<i class="bi bi-wifi-off"></i> Network error — please check your connection and try again.'
            );
            setButtonsDisabled(false);
        }
    }

    // =================================================================
    // Helper — status alert
    // =================================================================
    /**
     * Show a Bootstrap alert in the status area.
     *
     * @param {string} type — Bootstrap alert type (success, danger, info, warning)
     * @param {string} html — innerHTML for the alert
     */
    function showStatus(type, html) {
        if (!statusAlert) return;
        statusAlert.className = "alert alert-" + type;
        statusAlert.innerHTML = html;
    }

    // =================================================================
    // Helper — toggle button disabled state
    // =================================================================
    function setButtonsDisabled(disabled) {
        [btnKeepLocal, btnAcceptMaster, btnMerge, btnSubmitMerge].forEach(function (btn) {
            if (btn) btn.disabled = disabled;
        });
    }
});
