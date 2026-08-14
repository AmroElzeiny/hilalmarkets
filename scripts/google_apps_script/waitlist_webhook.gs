/**
 * Hilal Markets waitlist receiver for a Google Apps Script Web App.
 *
 * THE CONTRACT THIS FILE IMPLEMENTS
 * ---------------------------------
 * The server builds one fixed request body, in exactly one place:
 * `src/ai_market_monitor/services/waitlist_sheet_contract.py`. It sends six fields and
 * no others:
 *
 *   secret, email, name, source, country, status
 *
 * Until 14 August 2026 this file read a different set. It authorised on `webhook_secret`
 * and required `event_id` and `submitted_at`, none of which the server sends. Deploying
 * it as it stood would have answered `unauthorized` to every single signup, and the
 * rejection would have looked like an ordinary delivery failure in the retry log.
 *
 * `WAITLIST_SHEET_FIELDS` in the Python module is the contract. This file is the other
 * half of it. Changing either one without the other breaks every signup silently, so
 * `tests/unit/test_invariant_waitlist_sheet_payload.py` reads both and fails if the two
 * stop agreeing.
 *
 * WHAT IS DEDUPLICATED, AND WHAT IS NOT
 * -------------------------------------
 * The server sends no delivery id, so a retry cannot be recognised by one. Duplicates
 * are prevented by email address instead, which is the right key for a waitlist: one
 * person, one row. The email check runs before the append, so a retry of a delivery
 * that already succeeded adds nothing.
 *
 * `Joined At (UTC)` is the moment this script received the signup, not the moment the
 * person submitted it. The server does not send its own timestamp, and inventing one
 * from an absent field is how the previous version rejected everything. The authoritative
 * submission time stays in `waitlist_signups` in the product's own database.
 *
 * Script properties required:
 *   WAITLIST_WEBHOOK_SECRET: same value as WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET.
 *   WAITLIST_SPREADSHEET_ID: ID from the destination Google Sheet URL.
 * Optional:
 *   WAITLIST_SHEET_NAME: defaults to "Waitlist".
 */

const WAITLIST_HEADERS = [
  'Email Address',
  'Joined At (UTC)',
  'Country',
  'Signup Source',
  'Status',
  'Notes',
];

/**
 * Layouts this script can bring forward, newest first.
 *
 * Both older shapes carried a `System Delivery ID` column fed by the `event_id` the
 * server no longer sends. The column is dropped rather than kept empty: a column that
 * can never be filled again reads as missing data instead of retired data.
 */
const WAITLIST_HEADERS_WITH_DELIVERY_ID = [
  'Email Address',
  'Joined At (UTC)',
  'Country',
  'Signup Source',
  'Campaign',
  'Status',
  'Notes',
  'System Delivery ID',
];

const WAITLIST_HEADERS_WITH_CONSENT = [
  'Email Address',
  'Joined At (UTC)',
  'Country',
  'Signup Source',
  'Campaign',
  'Beta Testing Consent',
  'Status',
  'Notes',
  'System Delivery ID',
];

const WAITLIST_STATUS_OPTIONS = [
  'New',
  'Invited',
  'Joined Beta',
  'Follow Up',
  'Not Interested',
];

const WAITLIST_STATUS_COLUMN = 5;
const WAITLIST_NOTES_COLUMN = 6;
const WAITLIST_COLUMN_COUNT = WAITLIST_HEADERS.length;

function doPost(event) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const expectedSecret = properties.getProperty('WAITLIST_WEBHOOK_SECRET') || '';
    const spreadsheetId = properties.getProperty('WAITLIST_SPREADSHEET_ID') || '';
    const sheetName = properties.getProperty('WAITLIST_SHEET_NAME') || 'Waitlist';
    const payload = JSON.parse((event && event.postData && event.postData.contents) || '{}');

    // `secret`, matching `WAITLIST_SHEET_FIELDS[0]`. Reading `webhook_secret` here is
    // what rejected every signup, so the name is checked against the contract by test.
    if (!expectedSecret || !spreadsheetId || payload.secret !== expectedSecret) {
      return jsonResponse_({ ok: false, error: 'unauthorized' });
    }

    const email = safeCellText_(payload.email);
    if (!email || email.indexOf('@') === -1) {
      return jsonResponse_({ ok: false, error: 'invalid_signup' });
    }

    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    let sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) sheet = spreadsheet.insertSheet(sheetName);

    prepareWaitlistSheet_(sheet);

    // One person, one row. This is the whole retry defence now that no delivery id is
    // sent, and it is checked before the append rather than after it.
    if (findEmail_(sheet, email)) {
      return jsonResponse_({ ok: true, created: false });
    }

    sheet.appendRow([
      email,
      new Date(),
      safeCellText_(payload.country || 'unknown'),
      safeCellText_(payload.source || 'Direct'),
      'New',
      '',
    ]);
    formatWaitlistRows_(sheet);
    return jsonResponse_({ ok: true, created: true });
  } catch (error) {
    return jsonResponse_({ ok: false, error: 'request_failed' });
  } finally {
    lock.releaseLock();
  }
}

function prepareWaitlistSheet_(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, WAITLIST_COLUMN_COUNT).setValues([WAITLIST_HEADERS]);
  } else {
    const currentHeaders = sheet
      .getRange(1, 1, 1, Math.max(sheet.getLastColumn(), WAITLIST_COLUMN_COUNT))
      .getDisplayValues()[0];
    if (!matchesHeaderSet_(currentHeaders, WAITLIST_HEADERS)) upgradeSheetLayout_(sheet);
  }

  sheet
    .getRange(1, 1, 1, WAITLIST_COLUMN_COUNT)
    .setValues([WAITLIST_HEADERS])
    .setBackground('#2b2e35')
    .setFontColor('#ffffff')
    .setFontWeight('bold');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 260);
  sheet.setColumnWidth(2, 170);
  sheet.setColumnWidth(3, 100);
  sheet.setColumnWidth(4, 150);
  sheet.setColumnWidth(WAITLIST_STATUS_COLUMN, 130);
  sheet.setColumnWidth(WAITLIST_NOTES_COLUMN, 300);
  formatWaitlistRows_(sheet);
}

function matchesHeaderSet_(row, expected) {
  return expected.every((header, index) => String(row[index] || '').trim() === header);
}

/**
 * Bring a sheet in any recognised earlier shape to the current layout, losing no rows.
 *
 * Anything unrecognised throws, so an unknown sheet is left exactly as it is rather than
 * being overwritten by a guess.
 */
function upgradeSheetLayout_(sheet) {
  const values = sheet.getDataRange().getValues();
  const rows = values.filter(row => row.some(value => String(value).trim() !== ''));
  if (rows.length === 0) return;

  let migrated;
  if (matchesHeaderSet_(rows[0], WAITLIST_HEADERS_WITH_CONSENT)) {
    // Email, Joined, Country, Source, Campaign, Consent, Status, Notes, DeliveryId
    migrated = rows.slice(1).map(row => carryRow_(row, 6, 7));
  } else if (matchesHeaderSet_(rows[0], WAITLIST_HEADERS_WITH_DELIVERY_ID)) {
    // Email, Joined, Country, Source, Campaign, Status, Notes, DeliveryId
    migrated = rows.slice(1).map(row => carryRow_(row, 5, 6));
  } else {
    migrated = migrateLegacyRows_(rows);
  }

  const existingFilter = sheet.getFilter();
  if (existingFilter) existingFilter.remove();
  sheet.clear();
  sheet.getRange(1, 1, 1, WAITLIST_COLUMN_COUNT).setValues([WAITLIST_HEADERS]);
  if (migrated.length > 0) {
    sheet.getRange(2, 1, migrated.length, WAITLIST_COLUMN_COUNT).setValues(migrated);
  }
}

/** One older row carried across, told where its Status and Notes cells sit. */
function carryRow_(row, statusIndex, notesIndex) {
  const joinedAt = new Date(String(row[1] || ''));
  if (Number.isNaN(joinedAt.getTime())) throw new Error('waitlist_legacy_timestamp_invalid');
  return [
    safeCellText_(row[0]),
    joinedAt,
    safeCellText_(row[2] || 'unknown'),
    safeCellText_(row[3] || 'Direct'),
    safeCellText_(row[statusIndex] || 'New'),
    safeCellText_(row[notesIndex] || ''),
  ];
}

function migrateLegacyRows_(rows) {
  const legacyRows = rows.slice();
  if (legacyRows.length > 0 && isLegacyHeader_(legacyRows[0])) legacyRows.shift();
  const canMigrate = legacyRows.every(row => {
    const deliveryId = String(row[0] || '');
    const email = String(row[1] || '');
    return deliveryId.startsWith('waitlist:') && email.indexOf('@') !== -1;
  });
  if (!canMigrate) throw new Error('waitlist_sheet_schema_unrecognized');

  return legacyRows.map(row => {
    const submittedAt = new Date(String(row[2] || ''));
    if (Number.isNaN(submittedAt.getTime())) {
      throw new Error('waitlist_legacy_timestamp_invalid');
    }
    return [
      safeCellText_(row[1]),
      submittedAt,
      safeCellText_(row[3] || 'unknown'),
      safeCellText_(row[5] || row[4] || 'Direct'),
      'New',
      '',
    ];
  });
}

function isLegacyHeader_(row) {
  const first = String(row[0] || '').trim().toLowerCase().replace(/[_-]/g, ' ');
  const second = String(row[1] || '').trim().toLowerCase().replace(/[_-]/g, ' ');
  return first.indexOf('event') !== -1 && second.indexOf('email') !== -1;
}

function findEmail_(sheet, email) {
  if (sheet.getLastRow() < 2) return null;
  return sheet
    .getRange(2, 1, sheet.getLastRow() - 1, 1)
    .createTextFinder(email)
    .matchCase(false)
    .matchEntireCell(true)
    .findNext();
}

function formatWaitlistRows_(sheet) {
  const rowCount = Math.max(sheet.getLastRow() - 1, 1);
  sheet.getRange(2, 2, rowCount, 1).setNumberFormat('yyyy-mm-dd hh:mm:ss "UTC"');
  const validation = SpreadsheetApp.newDataValidation()
    .requireValueInList(WAITLIST_STATUS_OPTIONS, true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(2, WAITLIST_STATUS_COLUMN, rowCount, 1).setDataValidation(validation);
  sheet.getRange(2, 1, rowCount, WAITLIST_COLUMN_COUNT).setVerticalAlignment('middle');
  sheet.getRange(2, WAITLIST_NOTES_COLUMN, rowCount, 1).setWrap(true);
  refreshWaitlistFilter_(sheet);
}

function refreshWaitlistFilter_(sheet) {
  const requiredRows = Math.max(sheet.getLastRow(), 2);
  const existing = sheet.getFilter();
  if (existing) {
    const range = existing.getRange();
    if (
      range.getNumRows() === requiredRows &&
      range.getNumColumns() === WAITLIST_COLUMN_COUNT
    ) {
      return;
    }
    existing.remove();
  }
  sheet.getRange(1, 1, requiredRows, WAITLIST_COLUMN_COUNT).createFilter();
}

function safeCellText_(value) {
  const text = String(value || '').trim().slice(0, 500);
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
}

function jsonResponse_(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
