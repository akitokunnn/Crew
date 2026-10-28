"use strict";
// Include feature-owned frontend contracts in the repository's standard
// node --test plugin/tests/test_*.js entry without changing the shared CI job.
require("../crew/web/tests/crew-client.test.cjs");
require("../crew/web/tests/conversation-entry.test.cjs");
require("../crew/web/tests/personal-workspace.test.cjs");
require("../crew/web/tests/scoped-settings.test.cjs");
require("../crew/web/tests/remote-settings.test.cjs");
require("../crew/web/tests/workbench-state.test.cjs");
