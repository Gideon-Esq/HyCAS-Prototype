from flask import Flask, render_template, request, jsonify
import requests
import xml.etree.ElementTree as ET

app = Flask(__name__)

# --- 1. HELPER: CONNECTION LOGIC ---
def query_loc_sru(query_string):
    url = "http://lx2.loc.gov:210/LCDB"
    params = {
        "operation": "searchRetrieve",
        "version": "1.1",
        "query": query_string,
        "maximumRecords": "1",
        "recordSchema": "mods"
    }
    return requests.get(url, params=params, timeout=10)

# --- 2. HELPER: PARSING LOGIC ---
def parse_loc_xml(xml_content):
    namespaces = {'zs': 'http://www.loc.gov/zing/srw/', 'mods': 'http://www.loc.gov/mods/v3'}
    root = ET.fromstring(xml_content)
    mods = root.find('.//mods:mods', namespaces)

    if mods is None: return None

    # Title
    title = mods.findtext('.//mods:titleInfo/mods:title', default="Unknown", namespaces=namespaces)
    subtitle = mods.findtext('.//mods:titleInfo/mods:subTitle', default="", namespaces=namespaces)
    full_title = f"{title}: {subtitle}" if subtitle else title

    # Author
    author = mods.findtext('.//mods:name[@usage="primary"]/mods:namePart', default="", namespaces=namespaces)
    if not author: 
        author = mods.findtext('.//mods:name/mods:namePart', default="Unknown", namespaces=namespaces)

    # Imprint
    pub = mods.findtext('.//mods:originInfo/mods:agent/mods:namePart', default="", namespaces=namespaces)
    place = mods.findtext('.//mods:originInfo/mods:place/mods:placeTerm[@type="text"]', default="", namespaces=namespaces)
    year = mods.findtext('.//mods:originInfo/mods:dateIssued', default="", namespaces=namespaces)

    # Physical Description
    extent = mods.findtext('.//mods:physicalDescription/mods:extent', default="", namespaces=namespaces)

    # Call Numbers
    lcc = mods.findtext('.//mods:classification[@authority="lcc"]', default="", namespaces=namespaces)
    ddc = mods.findtext('.//mods:classification[@authority="ddc"]', default="", namespaces=namespaces)

    # Subjects
    subjects = []
    for s in mods.findall('.//mods:subject', namespaces):
        t = s.find('mods:topic', namespaces)
        if t is not None: subjects.append(t.text)

    return {
        "success": True,
        "source": "Library of Congress (Official)",
        "title": full_title,
        "author": author,
        "publisher": pub,
        "place": place,
        "year": year,
        "physical_desc": extent,
        "lcc": lcc,
        "ddc": ddc,
        "subjects": ", ".join(subjects[:8])
    }

# --- 3. MAIN FUNCTION ---
def fetch_catalog_data(isbn):
    raw_input = isbn.strip()
    clean_isbn = raw_input.replace("-", "")
    
    print(f"\n--- SEARCHING FOR {clean_isbn} ---")

    # ==========================================
    # CHOICE 1: LIBRARY OF CONGRESS (LOC)
    # ==========================================
    queries = []
    
    # Strategy A: Try Clean ISBN 
    queries.append(f'bath.isbn="{clean_isbn}"')

    # Strategy B: Try Hyphenated (Your manual logic)
    if len(clean_isbn) == 13:
        prefix = clean_isbn[:3]
        group = clean_isbn[3]
        
        if group in ['0', '1']:
            rest = clean_isbn[4:]
            queries.append(f'bath.isbn="{prefix}-{group}-{rest[:2]}-{rest[2:8]}-{rest[8]}"')
            queries.append(f'bath.isbn="{prefix}-{group}-{rest[:3]}-{rest[3:8]}-{rest[8]}"')
            queries.append(f'bath.isbn="{prefix}-{group}-{rest[:4]}-{rest[4:8]}-{rest[8]}"')

        elif clean_isbn.startswith("978978"):
            queries.append(f'bath.isbn="978-978-{clean_isbn[6:9]}-{clean_isbn[9:12]}-{clean_isbn[12]}"')

    # Run LOC Loop
    unique_queries = sorted(list(set(queries)), key=queries.index)

    for q in unique_queries:
        try:
            print(f"📡 LOC Query: {q}")
            response = query_loc_sru(q)
            
            if "numberOfRecords>0<" in response.text:
                print("   ❌ No hits.")
                continue 
            
            print("   ✅ SUCCESS! Record found in LOC.")
            return parse_loc_xml(response.content)

        except Exception as e:
            print(f"   Error: {e}")

    print("⚠️ LOC failed. Switching to Open Library...")

    # ==========================================
    # CHOICE 2: OPEN LIBRARY
    # ==========================================
    try:
        ol_url = "https://openlibrary.org/api/books"
        params = {
            'bibkeys': f'ISBN:{clean_isbn}',
            'format': 'json',
            'jscmd': 'data'
        }
        ol_resp = requests.get(ol_url, params=params, timeout=5)
        ol_data = ol_resp.json()
        
        key = f'ISBN:{clean_isbn}'
        if key in ol_data:
            book = ol_data[key]
            
            # Extract authors safely
            authors = [a.get('name') for a in book.get('authors', [])]
            
            # Extract subjects safely
            subjects = [s.get('name') for s in book.get('subjects', [])]

            print("   ✅ SUCCESS! Record found in Open Library.")
            return {
                "success": True,
                "source": "Open Library",
                "title": book.get('title', 'Unknown'),
                "isbn": isbn,
                "author": ", ".join(authors),
                "publisher": book.get('publishers', [{}])[0].get('name', ''),
                "place": book.get('publish_places', [{}])[0].get('name', ''),
                "year": book.get('publish_date', ''),
                "physical_desc": f"{book.get('number_of_pages', '?')} pages",
                "lcc": book.get('identifiers', {}).get('lccn', [''])[0],
                "ddc": book.get('classifications', {}).get('dewey_decimal_class', [''])[0],
                "subjects": ", ".join(subjects[:8])
            }
    except Exception as e:
        print(f"   Open Library Error: {e}")

    print("⚠️ Open Library failed. Switching to Google Books...")

    # ==========================================
    # CHOICE 3: GOOGLE BOOKS
    # ==========================================
    try:
        g_url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_isbn}"
        g_data = requests.get(g_url, timeout=5).json()
        
        if g_data.get("totalItems", 0) > 0:
            book = g_data['items'][0]['volumeInfo']
            print("   ✅ SUCCESS! Record found in Google Books.")
            return {
                "success": True,
                "source": "Google Books",
                "title": book.get('title'),
                "isbn": isbn,
                "author": ", ".join(book.get('authors', [])),
                "publisher": book.get('publisher', ""),
                "place": "", # Google rarely provides place
                "year": book.get('publishedDate', ""),
                "physical_desc": f"{book.get('pageCount', '?')} pages",
                "lcc": "",
                "ddc": "",
                "subjects": ", ".join(book.get('categories', []))
            }
    except Exception as e:
        print(f"   Google Books Error: {e}")

    return {"success": False, "error": "Book not found in LOC, Open Library, or Google."}

# --- ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search')
def search_api():
    isbn = request.args.get('isbn')
    result = fetch_catalog_data(isbn)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
