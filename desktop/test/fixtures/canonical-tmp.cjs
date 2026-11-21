'use strict';
// macOS's default temp dir is under /var, a symlink to /private/var. Crew refuses symlinked HOME paths and
// resolves real paths, so tests would see two spellings of one directory. CI already uses a resolved TMPDIR;
// this does the same for local runs. Test files are child processes and inherit the variable.
if (process.platform === 'darwin') {
  const fs = require('node:fs'), os = require('node:os');
  process.env.TMPDIR = fs.realpathSync(os.tmpdir());
}
