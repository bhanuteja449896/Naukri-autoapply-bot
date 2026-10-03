import re
from datetime import datetime
from sheets_client import get_sheets_client

svc = get_sheets_client()

sheets = {
    'Rahul': '1l923Cd-ZkqSceji5RW674PHNbvLoqggSgQy-QQ9TuYQ',
    'Bhanu': '1iWItnmTsDEwM5WHDdy7G2FqOCy-N_c7x0bMvmOK39g0',
    'Kiran': '10j2BHXrR8KddSW3UhYMniLs5bn5DLv9hWk9nWqAHv6o',
}

def parse_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return datetime.min
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return datetime.min

for name, sid in sheets.items():
    print(f"\n=======================================================")
    print(f"Sorting & cleaning {name} ({sid}) - OLDEST ON TOP")
    print(f"=======================================================")
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_objs = meta.get('sheets', [])
    existing_sheets = {s['properties']['title']: s['properties']['sheetId'] for s in sheet_objs}
    print(f"Existing tabs: {list(existing_sheets.keys())}")
    
    # Delete _test_ tab if present
    if '_test_' in existing_sheets:
        print(f"  Removing temporary tab '_test_'...")
        try:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={"requests": [{"deleteSheet": {"sheetId": existing_sheets['_test_']}}]}
            ).execute()
            print("  Removed '_test_' tab successfully.")
        except Exception as e:
            print(f"  Error removing '_test_': {e}")
            
    for tab in ['Direct Applied', 'Apply on Website']:
        if tab not in existing_sheets:
            continue
        res = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab}'!A:Z").execute()
        rows = res.get('values', [])
        if not rows:
            print(f"  [{tab}] is empty.")
            continue
            
        header = rows[0]
        data_rows = rows[1:]
        
        date_col = -1
        for idx, col in enumerate(header):
            if "date" in col.lower():
                date_col = idx
                break
        if date_col == -1:
            date_col = 6 if tab == 'Direct Applied' else 7
            
        # Deduplicate by job URL (column 2)
        seen_urls = set()
        deduped = []
        for r in data_rows:
            url = r[2] if len(r) > 2 else ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            deduped.append(r)
            
        # Sort ascending (Oldest on top, newest at bottom)
        sorted_rows = sorted(
            deduped,
            key=lambda r: parse_date(r[date_col] if len(r) > date_col else "")
        )
        
        # Clear existing tab
        svc.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{tab}'!A:Z").execute()
        
        # Write back header + sorted rows
        body = {"values": [header] + sorted_rows}
        svc.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body=body
        ).execute()
        
        print(f"  [{tab}]: Successfully updated {len(sorted_rows)} rows (Oldest on top).")
        if sorted_rows:
            print(f"    Row 2 (Oldest): {sorted_rows[0][date_col]} | {sorted_rows[0][1]} | {sorted_rows[0][0][:30]}")
            print(f"    Row {len(sorted_rows)+1} (Newest): {sorted_rows[-1][date_col]} | {sorted_rows[-1][1]} | {sorted_rows[-1][0][:30]}")

print("\nAll 3 sheets successfully reordered to Oldest on Top!")
