/**
 * Hilal Markets waitlist receiver for a Google Apps Script Web App.
 *
 * Visible columns are intentionally business-friendly. Delivery metadata is
 * retained in one hidden column so retries remain idempotent.
 *
 * Script properties required:
 *   WAITLIST_WEBHOOK_SECRET: same value as the server environment setting.
 *   WAITLIST_SPREADSHEET_ID: ID from the destination Google Sheet URL.
 * Optional:
 *   WAITLIST_SHEET_NAME: defaults to "Waitlist".
 */

const WAITLIST_HEADERS = [
  'Email Address',
  'Joined At (UTC)',
  'Country',
  'Signup Source',
  'Campaign',
  'Status',
  'Notes',
  'System Delivery ID',
];

/**
 * The layout used while the form asked for beta-testing consent.
 *
 * The question was withdrawn: the box was offered already ticked, so its answer was not
 * a choice anybody made. A sheet still holding that column is brought back to the layout
 * above. Every row keeps its email, date, source, status and notes; only the consent cell
 * is dropped, because it never held a real answer to keep.
 */
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

const WAITLIST_STATUS_COLUMN = 6;
const WAITLIST_NOTES_COLUMN = 7;
const WAITLIST_DELIVERY_ID_COLUMN = 8;
const WAITLIST_VISIBLE_COLUMNS = WAITLIST_DELIVERY_ID_COLUMN - 1;

function doPost(event) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const properties = PropertiesService.getScriptProperties();
    const expectedSecret = properties.getProperty('WAITLIST_WEBHOOK_SECRET') || '';
    const spreadsheetId = properties.getProperty('WAITLIST_SPREADSHEET_ID') || '';
    const sheetName = properties.getProperty('WAITLIST_SHEET_NAME') || 'Waitlist';
    const payload = JSON.parse((event && event.postData && event.postData.contents) || '{}');

    if (!expectedSecret || !spreadsheetId || payload.webhook_secret !== expectedSecret) {
      return jsonResponse_({ ok: false, error: 'unauthorized' });
    }

    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    let sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) sheet = spreadsheet.insertSheet(sheetName);

    prepareWaitlistSheet_(sheet);

    const deliveryId = String(payload.event_id || '').slice(0, 255);
    if (!deliveryId) return jsonResponse_({ ok: false, error: 'event_id_required' });
    if (findDeliveryId_(sheet, deliveryId)) {
      return jsonResponse_({ ok: true, created: false });
    }

    const email = safeCellText_(payload.email);
    const submittedAt = new Date(String(payload.submitted_at || ''));
    if (!email || Number.isNaN(submittedAt.getTime())) {
      return jsonResponse_({ ok: false, error: 'invalid_signup' });
    }
    if (findEmail_(sheet, email)) {
      return jsonResponse_({ ok: true, created: false });
    }

    const source = safeCellText_(payload.utm_source || payload.source_page || 'Direct');
    const campaign = safeCellText_(payload.utm_campaign || '');
    sheet.appendRow([
      email,
      submittedAt,
      safeCellText_(payload.country || 'Unknown'),
      source,
      campaign,
      'New',
      '',
      deliveryId,
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
    sheet.getRange(1, 1, 1, WAITLIST_HEADERS.length).setValues([WAITLIST_HEADERS]);
  } else {
    const currentHeaders = sheet
      .getRange(1, 1, 1, Math.min(sheet.getLastColumn(), WAITLIST_HEADERS.length))
      .getDisplayValues()[0];
    if (!headersMatch_(currentHeaders)) upgradeSheetLayout_(sheet);
  }

  const header = sheet.getRange(1, 1, 1, WAITLIST_HEADERS.length);
  header
    .setValues([WAITLIST_HEADERS])
    .setBackground('#2b2e35')
    .setFontColor('#ffffff')
    .setFontWeight('bold');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 260);
  sheet.setColumnWidth(2, 170);
  sheet.setColumnWidth(3, 100);
  sheet.setColumnWidth(4, 150);
  sheet.setColumnWidth(5, 170);
  sheet.setColumnWidth(WAITLIST_STATUS_COLUMN, 130);
  sheet.setColumnWidth(WAITLIST_NOTES_COLUMN, 300);
  sheet.hideColumns(WAITLIST_DELIVERY_ID_COLUMN);
  formatWaitlistRows_(sheet);
}

function headersMatch_(headers) {
  return WAITLIST_HEADERS.every((header, index) => headers[index] === header);
}

function matchesHeaderSet_(row, expected) {
  return expected.every((header, index) => String(row[index] || '').trim() === header);
}

/**
 * Bring a sheet in any other recognised shape to the current layout, losing no rows.
 *
 * Two other shapes are recognised: the one that carried the withdrawn consent column,
 * and the oldest delivery-id-first layout. Anything else throws, so an unknown sheet is
 * left exactly as it is rather than being overwritten by a guess.
 */
function upgradeSheetLayout_(sheet) {
  const values = sheet.getDataRange().getValues();
  const rows = values.filter(row => row.some(value => String(value).trim() !== ''));
  if (rows.length === 0) return;

  let migrated;
  if (matchesHeaderSet_(rows[0], WAITLIST_HEADERS_WITH_CONSENT)) {
    migrated = rows.slice(1).map(row => {
      const joinedAt = new Date(String(row[1] || ''));
      if (Number.isNaN(joinedAt.getTime())) throw new Error('waitlist_legacy_timestamp_invalid');
      return [
        safeCellText_(row[0]),
        joinedAt,
        safeCellText_(row[2] || 'Unknown'),
        safeCellText_(row[3] || 'Direct'),
        safeCellText_(row[4] || ''),
        // row[5] was the consent cell and is dropped. Status, notes and the delivery id
        // shift back one place; every one of them is carried across.
        safeCellText_(row[6] || 'New'),
        safeCellText_(row[7] || ''),
        String(row[8] || '').slice(0, 255),
      ];
    });
  } else {
    migrated = migrateLegacyRows_(rows);
  }

  const existingFilter = sheet.getFilter();
  if (existingFilter) existingFilter.remove();
  sheet.clear();
  sheet.getRange(1, 1, 1, WAITLIST_HEADERS.length).setValues([WAITLIST_HEADERS]);
  if (migrated.length > 0) {
    sheet.getRange(2, 1, migrated.length, WAITLIST_HEADERS.length).setValues(migrated);
  }
}

function migrateLegacyRows_(rows) {
  const legacyRows = rows.slice();
  if (legacyRows.length > 0 && isLegacyHeader_(legacyRows[0])) legacyRows.shift();
  const canMigrate = legacyRows.every(row => {
    const deliveryId = String(row[0] || '');
    const email = String(row[1] || '');
    return deliveryId.startsWith('waitlist:') && email.includes('@');
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
      safeCellText_(row[3] || 'Unknown'),
      safeCellText_(row[5] || row[4] || 'Direct'),
      safeCellText_(row[7] || ''),
      'New',
      '',
      String(row[0]).slice(0, 255),
    ];
  });
}

function isLegacyHeader_(row) {
  const first = String(row[0] || '').trim().toLowerCase().replace(/[_-]/g, ' ');
  const second = String(row[1] || '').trim().toLowerCase().replace(/[_-]/g, ' ');
  return first.includes('event') && second.includes('email');
}

function findDeliveryId_(sheet, deliveryId) {
  if (sheet.getLastRow() < 2) return null;
  return sheet
    .getRange(2, WAITLIST_DELIVERY_ID_COLUMN, sheet.getLastRow() - 1, 1)
    .createTextFinder(deliveryId)
    .matchEntireCell(true)
    .findNext();
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
  sheet.getRange(2, 1, rowCount, WAITLIST_VISIBLE_COLUMNS).setVerticalAlignment('middle');
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
      range.getNumColumns() === WAITLIST_VISIBLE_COLUMNS
    ) {
      return;
    }
    existing.remove();
  }
  sheet.getRange(1, 1, requiredRows, WAITLIST_VISIBLE_COLUMNS).createFilter();
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
