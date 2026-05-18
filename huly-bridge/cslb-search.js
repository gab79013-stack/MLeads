#!/usr/bin/env node
/**
 * cslb-search.js
 * Search CSLB by business name using the www2.cslb.ca.gov ASP.NET form.
 * 
 * Usage:
 *   node cslb-search.js "Pacific Roofing" "Oakland"
 *   node cslb-search.js --batch contacts/C-39\ ROOFING.csv --limit 20
 * 
 * Output: JSON array of {license_number, business_name, status, city}
 */

const axios = require('axios');
const { JSDOM } = require('jsdom');

const BASE_URL = 'https://www2.cslb.ca.gov/onlineservices/checklicenseII/checklicense.aspx';

async function searchByName(name, city = '') {
  const jar = new axios.CookieJar();
  const client = axios.create({
    baseURL: 'https://www2.cslb.ca.gov',
    timeout: 20000,
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Accept': 'text/html,application/xhtml+xml',
    },
  });

  // Step 1: Load page and get ViewState
  const page1 = await client.get(BASE_URL);
  const dom1 = new JSDOM(page1.data);
  const doc1 = dom1.window.document;

  const viewstate = doc1.querySelector('input[name="__VIEWSTATE"]')?.value || '';
  const viewstategen = doc1.querySelector('input[name="__VIEWSTATEGENERATOR"]')?.value || '';
  const eventval = doc1.querySelector('input[name="__EVENTVALIDATION"]')?.value || '';

  // Step 2: Submit search
  const formData = new URLSearchParams();
  formData.append('__VIEWSTATE', viewstate);
  formData.append('__VIEWSTATEGENERATOR', viewstategen);
  formData.append('__EVENTVALIDATION', eventval);
  formData.append('ctl00$LeftColumnMiddle$LicNo', '');
  formData.append('MainContent_NextName', name.substring(0, 50));
  formData.append('MainContent_SearchType', 'BusinessName');
  formData.append('MainContent_btnSearch', 'Search');

  await new Promise(r => setTimeout(r, 2000)); // Rate limit
  const page2 = await client.post(BASE_URL, formData.toString(), {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });

  // Step 3: Parse results
  const dom2 = new JSDOM(page2.data);
  const doc2 = dom2.window.document;
  const results = [];

  // Find license number links
  const links = doc2.querySelectorAll('a[href*="LicNum="]');
  for (const link of links) {
    const href = link.getAttribute('href') || '';
    const match = href.match(/LicNum=(\d+)/);
    if (match) {
      const row = link.closest('tr');
      const cells = row ? row.querySelectorAll('td') : [];
      results.push({
        license_number: match[1],
        business_name: link.textContent.trim(),
        city: cells[2]?.textContent.trim() || city,
        status: cells[3]?.textContent.trim() || '',
      });
    }
  }

  return results.slice(0, 10);
}

// CLI
const args = process.argv.slice(2);
if (args.length === 0) {
  console.log('Usage: node cslb-search.js "Business Name" [City]');
  process.exit(1);
}

const name = args[0];
const city = args[1] || '';

searchByName(name, city).then(results => {
  console.log(JSON.stringify(results, null, 2));
}).catch(err => {
  console.error('Error:', err.message);
  process.exit(1);
});
