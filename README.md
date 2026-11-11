# CurbWise

Finding street parking in New York City can mean checking a lot of signs and figuring out which rules apply at a particular time. I built CurbWise to make that easier. It's a parking research tool for NYC drivers, especially people visiting a neighborhood they don't know well.

Enter a destination, when you plan to arrive, and how long you'll stay. CurbWise looks up nearby parking signs, meter schedules, and street-cleaning rules, then shows the relevant findings on a map. A Lead agent coordinates three Researchers for signs, meters, and exceptions; the app checks the rules against your full parking window before summarizing the results.

It doesn't know whether a spot is empty or guarantee that parking is legal. When the records don't answer something, it says so and points out what you still need to check on the street.

## Data

CurbWise uses these public sources:

| Source | What it's used for |
| --- | --- |
| [NYC DOT Parking Signs](https://data.cityofnewyork.us/d/nfid-uabd) | Sign locations, street-cleaning times, and other restrictions |
| [NYC DOT Parking Meters](https://data.cityofnewyork.us/d/693u-uax6) | Meter locations, vehicle types, hours, and time limits |
| [NYC Street Centerline](https://data.cityofnewyork.us/d/inkn-q76z) | Finding and displaying nearby streets |
| [NYC Construction Closures](https://data.cityofnewyork.us/d/i6b5-j7bu) | Possible temporary restrictions |
| [NYC DOT ASP Calendar](https://www.nyc.gov/html/dot/html/motorist/alternate-side-parking.shtml) | Alternate-side parking suspensions |
| [NYC GeoSearch](https://geosearch.planninglabs.nyc/docs/) | Turning an address into coordinates |

There's no dataset to download or import first. The app queries NYC Open Data through the Socrata API at `https://data.cityofnewyork.us/resource/{dataset_id}.json`, using the location to retrieve relevant records. The ASP calendar and geocoding service are accessed separately.

## Sample queries and comparisons

I tested the same questions with CurbWise and with **Gemini 3.5 Flash**, using no tools and no system prompt for the baseline. CurbWise uses Gemini 3.5 Flash for its Lead and Writer, with Flash Lite for the three Researchers. The answers below were recorded on **November 11, 2026**. The tables show short, verbatim excerpts; the complete recorded responses are preserved below each comparison.

### 1. Street cleaning on West 109th Street

**Query:** "I'm parking on West 109th Street near Amsterdam Avenue on Tuesday, November 17, 2026, from 9 AM to 11 AM. What street-cleaning restrictions should I watch for?"

**Expected answer:** The south side has Tuesday/Friday street cleaning during this stay, but the hours vary by block. Between Amsterdam and Broadway, the restriction is **9:30–11 AM**; between Columbus and Amsterdam, it's **8:30–10 AM**. North-side cleaning records are for Monday/Thursday. Exact curb boundaries still need to be checked against the posted signs.

**Source:** [NYC Parking Signs API](https://data.cityofnewyork.us/resource/nfid-uabd.json), particularly `S-01858427` (Amsterdam–Broadway), `S-01369061` (Columbus–Amsterdam), `S-01640049`, and `S-01369051`.

| Gemini without tools | CurbWise |
| --- | --- |
| “Because it is a Tuesday, you do not have to worry about street cleaning on this side.” | “Street cleaning is active on **Tuesdays and Fridays from 9:30 AM to 11:00 AM**. This directly overlaps your requested stay.” |

The baseline put the Tuesday restriction on the wrong side of the street. CurbWise used the sign records to identify the affected side and the different hours on neighboring blocks.

<details>
<summary>Full recorded answers for example 1</summary>

#### Gemini without tools — full recorded answer
For your parking window on **Tuesday, November 17, 2026, from 9:00 AM to 11:00 AM** on West 109th Street near Amsterdam Avenue, here is what you need to watch out for to avoid a ticket:

### 1. Is Alternate Side Parking (ASP) in effect?
**Yes.** Tuesday, November 17, 2026, is a standard weekday. There are no major NYC parking holidays (such as Veterans Day, Thanksgiving, or Diwali) on or near this date that would cause ASP to be suspended.

### 2. Street-Cleaning Restrictions by Side of the Street
In this area of the Upper West Side/Morningside Heights, street cleaning rules typically follow this pattern:

*   **The SOUTH Side of W. 109th St. (Odd-numbered addresses):**

    *   **Rule:** Typically **Monday / Thursday** cleaning.

    *   **Status for you:** **SAFE.** Because it is a Tuesday, you do not have to worry about street cleaning on this side. **This is your best bet for stress-free parking.**

*   **The NORTH Side of W. 109th St. (Even-numbered addresses):**

    *   **Rule:** Typically **Tuesday / Friday** cleaning.

    *   **Status for you:** **RISKY/RESTRICTED.** Because you are parking on a Tuesday, this side will be subject to street cleaning.

    *   **The Time Window:** Depending on the exact block (between Broadway & Amsterdam vs. Amsterdam & Columbus), the Tuesday cleaning window is typically **11:30 AM to 1:00 PM** or **8:30 AM to 10:00 AM**.

        *   **If the sign says 8:30 AM – 10:00 AM:** You cannot park here during the first half of your stay.

        *   **If the sign says 11:30 AM – 1:00 PM:** You can technically park here starting at 9:00 AM, but you **must be gone by 11:00 AM sharp** to avoid getting a ticket as the restriction begins.

### 3. What if you park on Amsterdam Avenue instead?
If you can't find a spot on 109th Street and have to park on Amsterdam Avenue itself:

*   **West Side of Amsterdam Ave:** Typically Monday/Thursday rules (Safe on Tuesday).

*   **East Side of Amsterdam Ave:** Typically Tuesday/Friday rules, but the cleaning window is usually early (**7:30 AM to 9:00 AM**). If you park at exactly 9:00 AM, the street sweeper will have already passed, but you will likely have to pay a parking meter.

### Summary Strategy:
1. **Target the South side** of West 109th Street first. It is a Tuesday, so the South side is exempt from street cleaning.

2. If you must park on the North side, **read the red-and-white sign immediately**. If the Tuesday cleaning window overlaps with 9:00 AM–11:00 AM (e.g., 8:30–10:00 AM), find another spot.

#### CurbWise — full recorded answer
### 1. Direct Answer
Actionable investigation leads exist for parking near West 109th Street and Amsterdam Avenue on Tuesday, November 17, 2026, from 9:00 AM to 11:00 AM. However, the exact legality of parking at these locations remains unverified because digital records do not guarantee legal status or live physical space availability. You must verify all rules using physical signage on-site.

---

### 2. Best Street Sides to Investigate
The following areas represent the most promising street sides for investigation, ranked by their likelihood of availability based on known schedules:

*   **Amsterdam Avenue (East Side) — between West 108th Street and West 109th Street**

    *   **Classification:** Metered.

    *   **Details:** Associated passenger parking meters allow parking for up to 2 hours from Monday through Saturday, between 9:00 AM and 7:00 PM. The street cleaning restriction on this side operates on Mondays, Wednesdays, and Fridays (8:30 AM–9:00 AM), which does not conflict with your Tuesday morning stay.

    *   **On-Site Check:** Verify the exact boundaries of the metered zone using physical sign arrows. Check for any partial "No Standing Anytime" zones or nearby bus stops.

*   **West 109th Street (North Side) — between Amsterdam Avenue and Broadway**

    *   **Classification:** Unmetered.

    *   **Details:** The street cleaning restriction on this side operates on Mondays and Thursdays (9:30 AM–11:00 AM), which does not conflict with your Tuesday stay.

    *   **On-Site Check:** Watch for any localized "No Parking Anytime" signs and check the physical sign arrows to determine the boundaries of the unmetered spaces.

*   **West 109th Street (North Side) — between Columbus Avenue and Amsterdam Avenue**

    *   **Classification:** Unmetered.

    *   **Details:** The street cleaning restriction on this side operates on Mondays and Thursdays (8:30 AM–10:00 AM), which does not conflict with your Tuesday stay.

    *   **On-Site Check:** Look for physical signs indicating "No Standing Anytime" zones that may restrict parking on sections of this block.

*   **West 108th Street (North Side) — between Broadway and Amsterdam Avenue**

    *   **Classification:** Unmetered.

    *   **Details:** Street cleaning on this side is limited to Mondays and Thursdays (9:30 AM–11:00 AM), which does not overlap your stay.

    *   **On-Site Check:** Look out for nearby school-day standing restrictions (7:00 AM–4:00 PM) which may apply to parts of this block.

---

### 3. Restrictions to Watch
If parking on **West 109th Street**, you must actively avoid or watch for the following major street-cleaning conflicts during your Tuesday, 9:00 AM to 11:00 AM window:

*   **West 109th Street (South Side) — between Amsterdam Avenue and Broadway:** Street cleaning is active on **Tuesdays and Fridays from 9:30 AM to 11:00 AM**. This directly overlaps your requested stay. Additionally, school-day "No Standing" restrictions (7:00 AM–6:00 PM) apply to sections of this block.

*   **West 109th Street (South Side) — between Columbus Avenue and Amsterdam Avenue:** Street cleaning is active on **Tuesdays and Fridays from 8:30 AM to 10:00 AM**. This creates a partial overlap with your stay (from 9:00 AM to 10:00 AM).

*   **West 109th Street (South Side) — between Broadway and Riverside Drive:** Street cleaning is active on **Tuesdays and Fridays from 9:30 AM to 11:00 AM**. This directly overlaps your stay.

---

### 4. What Remains Unverified
*   **Curb Boundaries:** Street geometry data represents centerline estimates. The exact physical boundaries of the legal curb space and the placement of sign arrows are unverified.

*   **Local Rule Overlaps:** There are unparsed or complex sign plates (including localized loading zones, temporary construction permits, or school-day restrictions) that could override the general parking rules.

*   **Emergency Suspensions:** Static databases do **not** reflect real-time alternate side parking suspensions due to weather, emergencies, or sudden local changes.

---

### 5. Evidence
*   **Street Cleaning & Standing Restrictions (West 109th St - South Side):** Source ID `S-01858427` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

*   **Street Cleaning Restrictions (West 109th St - South Side, Columbus to Amsterdam):** Source ID `S-01369061` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

*   **Street Cleaning Restrictions (West 109th St - South Side, Broadway to Riverside):** Source ID `S-01443096` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

*   **Street Cleaning & General Restrictions (West 109th St - North Side):** Source IDs `S-01640049`, `S-01369051` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

*   **Street Cleaning & General Restrictions (West 108th St - North Side):** Source ID `S-395438` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

*   **Amsterdam Ave East Side Meters:** Meter IDs `1389694`, `1389693` ([NYC OpenData](https://data.cityofnewyork.us/d/693u-uax6)); Street Cleaning Source ID `P-01790757` ([NYC DOT](https://data.cityofnewyork.us/d/nfid-uabd))

***

**CurbWise is a research aid. Lead recommendations are investigation priorities, not determinations of legality or availability. Physical signs and temporary restrictions take precedence.**

</details>

### 2. Commercial versus passenger meters on West 44th Street

**Query:** "Can I park a passenger car near West 44th Street and Broadway on Tuesday, November 17, 2026, from 10 AM to noon? What do the parking meters allow?"

**Expected answer:** Meter `1433365` has a weekday **commercial-only** period from **7 AM–6 PM** (`3HR Com Mon-Fri 0700-1800`). Its weekday passenger period starts at **6 PM**, so the requested 10 AM–noon stay is not covered by that passenger schedule.

**Source:** [NYC Parking Meters API](https://data.cityofnewyork.us/resource/693u-uax6.json), meter `1433365`; nearby sign evidence from the [Parking Signs API](https://data.cityofnewyork.us/resource/nfid-uabd.json), including `P-01832493`, `P-01649172`, and `S-01314550`.

| Gemini without tools | CurbWise |
| --- | --- |
| “Only vehicles with commercial license plates (vans or trucks meeting NYC's strict commercial registration requirements) are permitted to park at these meters.” | “**Active Meters (South Side, Avenue of the Americas to 7th Ave):** 1433140, 1433365.” “Because your requested stay is from 10:00 AM to noon, passenger cars are ineligible to park at these meters.” |

The baseline recognized the general rule, so this isn't a case where it was simply wrong. CurbWise's advantage was checking specific meter records and their hours instead of relying on a neighborhood-wide assumption.

<details>
<summary>Full recorded answers for example 2</summary>

#### Gemini without tools — full recorded answer
Based on standard New York City traffic regulations and the specific geography of the Times Square area, **no, you cannot park a passenger car on the street near West 44th Street and Broadway on Tuesday, November 17, 2026, from 10:00 AM to noon.**

Here is a detailed breakdown of why, how the meters work, and your best alternatives:

### 1. The Calendar (Tuesday, Nov 17, 2026)
November 17, 2026, is a standard Tuesday. There are no major federal or city holidays on this date that would suspend parking regulations. Therefore, standard weekday Midtown parking rules will be strictly enforced.

### 2. Location Constraints (Times Square Core)
* **Broadway:** The section of Broadway passing through West 44th Street is part of the Times Square pedestrian plazas. It is closed to vehicular traffic entirely, meaning there is **zero street parking** on Broadway itself.

* **West 44th Street (between 6th and 8th Avenues):** This stretch is the heart of the Theater District (home to major Broadway theaters, hotels, and historic clubs). Because of this, the curbs are heavily restricted. Most of the block is designated as **"No Standing Anytime," "No Standing Except Authorized Vehicles,"** or **"No Parking (Hotel/Taxi Stand Zones)"** to allow for theater loading and passenger drop-offs.

### 3. What the Parking Meters Allow
If you do find a block in this immediate area with parking meters (such as portions of 6th or 8th Avenue, or nearby cross streets), **passenger cars are still prohibited from using them during these hours.**

* **Commercial-Only Rules:** In Midtown Manhattan, from 7:00 AM to 6:00 PM (or 7:00 PM) on weekdays, street parking meters are designated **"No Standing Except Trucks Loading and Unloading"** (often referred to as commercial metering).

* **Passenger Cars Prohibited:** Only vehicles with commercial license plates (vans or trucks meeting NYC's strict commercial registration requirements) are permitted to park at these meters.

* **The Penalty:** If you park a passenger car at one of these commercial meters, you cannot pay the meter. Even if you try to pay via the ParkNYC app or a physical Muni-Meter, your ticket will be invalid because your vehicle has passenger plates. You will almost certainly be ticketed (usually a $115+ fine) and your vehicle stands a very high chance of being towed.

### Recommendation: Where to Park Instead
If you must drive into the area, street parking is not a viable option. You should plan on using one of the following alternatives:

1. **Parking Garages:** There are several parking garages on West 44th Street, West 45th Street, and West 43rd Street between 6th and 8th Avenues. Because this is Times Square, drive-up rates are incredibly high (often $40–$60+ for two hours).

   * **Tip:** Use parking apps like **Spothero** or **ParkWhiz** to reserve a spot in advance. Doing so can often discount the rate by 30% to 50%.

2. **Public Transit:** If possible, taking the subway is highly recommended. The Times Square–42nd Street station (N, Q, R, W, S, 1, 2, 3, 7, A, C, E lines) is just one block away.

#### CurbWise — full recorded answer
### 1. Direct answer: whether actionable investigation leads exist
Actionable investigation leads do exist. There are 6 conditional leads near West 44th Street and Broadway that warrant on-site verification. The current legal status of these locations is classified as **INSUFFICIENT_EVIDENCE**, which means legality cannot be fully verified from available digital records—not that legal parking is unavailable.

---

### 2. Best street sides to investigate
The following street sides are the top recommended leads for physical investigation.

*   **Avenue of the Americas (West Side, between W 43rd St and W 44th St)**

    *   **Classification:** Metered (Meters: 1433774, 1433773)

    *   **Restriction Overlaps:** Active commercial meter hours cover the requested stay window (07:00–19:00 / 07:00–18:00, Monday–Friday). These commercial limits overlap your 10:00 AM to noon stay on Tuesday, which does not establish passenger vehicle eligibility.

    *   **On-Site Check:** Verify physical signs to see if passenger vehicle parking is allowed during these hours, and confirm the exact curb boundaries.

*   **Avenue of the Americas (East Side, adjacent to W 43rd St / W 44th St blocks)**

    *   **Classification:** Unmetered

    *   **On-Site Check:** This side has no matched meter records. Check the physical street signs to identify if parking is permitted or if unmapped restrictions apply.

*   **Avenue of the Americas (West Side, between W 44th St and W 45th St)**

    *   **Classification:** Metered (Meters: 1433775, 1433776 - both classified as Inactive)

    *   **On-Site Check:** Look for active physical meters or updated signage regarding passenger vehicle rules.

*   **Avenue of the Americas (West Side, between W 42nd St and W 43rd St)**

    *   **Classification:** Metered (Meter: 1433773)

    *   **Restriction Overlaps:** Active commercial meter hours (07:00–18:00) overlap the requested stay and do not indicate passenger eligibility.

    *   **On-Site Check:** Check for physical signs indicating any local passenger exceptions.

#### What do the parking meters on West 44th Street allow?
For a passenger car during the requested Tuesday stay (10:00 AM to noon), the parking meters on West 44th Street **do not permit passenger parking**.

*   **Active Meters (South Side, 5th Ave to Avenue of the Americas):** 1433043, 1433044

*   **Inactive Meter (South Side, 5th Ave to Avenue of the Americas):** 1433045

*   **Active Meters (South Side, Avenue of the Americas to 7th Ave):** 1433140, 1433365

*   **Active Meters (North Side, 5th Ave to Avenue of the Americas):** 1433038, 1433042

*   **Inactive Meter (North Side, 5th Ave to Avenue of the Americas):** 1433041

**Rule Details:** All of these meter groups are designated for **Commercial Vehicles Only** (3HR Com) from Monday to Friday between 07:00 and 18:00. Passenger vehicle parking (6HR Pas) is only permitted from 18:00 to 24:00 (6:00 PM to midnight). Because your requested stay is from 10:00 AM to noon, passenger cars are ineligible to park at these meters.

---

### 3. Restrictions to watch
Avoid parking in the following restricted areas nearby:

*   **West 44th Street (South Side, between 6th Ave and 5th Ave):** No Standing Anytime, and a designated Bus Stop zone (Source: P-01832493).

*   **West 44th Street (North Side, between 6th Ave and 5th Ave):** No Standing Anytime (Source: P-01649172).

*   **West 44th Street (North Side, between Broadway and Avenue of the Americas):** No Standing Anytime and No Parking Anytime (Source: S-01314550).

*   **West 44th Street (South Side, between 7th Ave and 6th Ave):** No Standing Anytime (Source: P-01800423).

---

### 4. What remains unverified
*   **Curb-Zone Scope:** The exact beginning and ending boundaries of the parking zones cannot be verified digitally. Physical signs and arrows on the street take precedence.

*   **Completeness of Digital Records:** The active or inactive status of specific physical meters, as well as temporary emergency or construction closures, are not reflected in the static dataset.

*   **Sign Arrow Directions:** The physical reach of double-sided and single-sided regulatory arrows has not been verified on-site.

---

### 5. Evidence
*   **Meter Schedule Data (Avenue of the Americas / West 44th Street):** Source ID: 1433774, 1433773, 1433775, 1433776, 1433043, 1433044, 1433045, 1433140, 1433365, 1433038, 1433042, 1433041 | [NYC Open Data Parking Meters](https://data.cityofnewyork.us/d/693u-uax6)

*   **Sign Restriction Data (West 44th Street):** Source IDs: P-01832493, P-01649172, S-01314550, P-01800423 | [NYC Open Data Street Sign Regulations](https://data.cityofnewyork.us/d/nfid-uabd)

---

CurbWise is a research aid. Lead recommendations are investigation priorities, not determinations of legality or availability. Physical signs and temporary restrictions take precedence.

</details>

### 3. Parking near Columbia University

**Query:** "I need street parking near Columbia University on Wednesday, November 18, 2026, from 10 AM to noon. Which streets or meters are worth checking, and what restrictions apply?"

**Expected answer:** Identify nearby meters whose passenger schedules cover the two-hour stay, check whether recorded street-cleaning restrictions overlap it, and flag local no-standing or loading zones that need an on-site sign check.

**Sources:** [NYC Parking Meters API](https://data.cityofnewyork.us/resource/693u-uax6.json), including `1218181`, `1218187`, `1218179`, `1389715`; [Parking Signs API](https://data.cityofnewyork.us/resource/nfid-uabd.json), including `P-01810504`, `P-01762026`, `P-01866191`, `S-01295546`.

| Gemini without tools | CurbWise |
| --- | --- |
| “Meters generally have a **2-hour limit**.” | “The passenger parking meter schedule operates Monday–Friday from 8:00 AM to 10:00 PM with a **6-hour maximum stay**, covering your entire parking window.” |

CurbWise found specific schedules that the baseline didn't have. It also flagged potentially conflicting restrictions and made clear that a meter schedule alone doesn't establish the exact legal curb space.

<details>
<summary>Full recorded answers for example 3</summary>

#### Gemini without tools — full recorded answer
Parking near Columbia University’s Morningside Heights campus on a Wednesday morning from 10:00 AM to 12:00 PM is highly competitive but manageable if you know where to look and how to navigate the local rules.

November 18, 2026, is a standard Wednesday with **no scheduled New York City Alternate Side Parking (ASP) holidays**, meaning all standard parking and street-cleaning regulations will be actively enforced.

Here is a breakdown of where to target, what rules to look out for, and strategies for success.

---

### 1. Metered Street Parking (Best for Convenience)
Because you only need parking for exactly two hours, metered parking is your most reliable bet. It has higher turnover than free street parking.

*   **Where to look:**

    *   **Broadway (110th St to 122nd St):** Both sides of the street have metered parking.

    *   **Amsterdam Ave (110th St to 122nd St):** Both sides have metered parking.

    *   **W 120th St (between Broadway and Amsterdam):** There are some metered spots here, though some sections are reserved for authorized vehicles or school buses.

*   **Rules & Restrictions:**

    *   Meters generally have a **2-hour limit**. If you park at 10:00 AM, you will be at the absolute limit of your stay by 12:00 PM.

    *   **Tip:** Download the **ParkNYC app** before you arrive. It allows you to pay from your phone and will alert you when your time is about to expire, though you cannot extend past the 2-hour limit on the same block.

### 2. Free Side-Street Parking (High Risk, High Reward)
If you want to avoid paying a meter, you can search the residential east-west side streets (111th St through 122nd St) between Riverside Drive and Manhattan Avenue.

*   **The Wednesday Advantage (Alternate Side Parking):**

    *   Many of the residential side streets in Morningside Heights operate on **Tuesday/Friday** or **Monday/Thursday** street-cleaning schedules.

    *   Because Wednesday is a "non-cleaning" day for these specific blocks, you do not have to worry about street sweepers on those streets.

    *   **The Catch:** Because there is no street cleaning on Wednesdays for these blocks, local residents "hibernate" their cars there. Finding an open spot at 10:00 AM on these blocks requires immense luck.

*   **Wednesday-Cleaning Streets (Avoid during restrictions):**

    *   Some select blocks and avenues do have Wednesday street-cleaning rules (often between 9:00 AM–10:30 AM or 11:30 AM–1:00 PM).

    *   **Do not park on a block that has Wednesday cleaning during your window.** Double-check the red-and-white broom signs on whatever block you find.

### 3. Riverside Drive & Claremont Avenue (Scenic & Quieter)
*   **Claremont Avenue (116th to 122nd St):** Located just behind Barnard College and the Columbia School of Journalism. It is a quieter, heavily residential street. It is worth doing a slow loop down Claremont to see if anyone is pulling out.

*   **Riverside Drive (110th to 122nd St):** Free parallel parking is available along Riverside Park. It is highly sought after, but because it is slightly further west, you may find someone leaving after a morning commute.

### 4. Morningside Drive (East of Campus)
*   **Morningside Drive (110th to 122nd St):** This street runs along the cliff of Morningside Park. Parking here is free, and because it is a slightly steeper walk up to the main campus, it occasionally has more vacancies than Broadway or Amsterdam.

---

### Critical Restrictions to Watch Out For
*   **School Zones:** Morningside Heights is dense with schools (The School at Columbia, Columbia Secondary, local daycare centers, and pre-schools). Many side-street blocks have **"No Parking/Standing School Hours" (typically 7:00 AM to 4:00 PM)**. Read the signs carefully; parking in these zones will result in an immediate ticket and potential tow.

*   **Fire Hydrants:** You must park at least **15 feet away** from any fire hydrant. NYC traffic agents actively measure this, and the fines are steep.

*   **Temporary Construction / Film Shoots:** Columbia’s campus and the surrounding historic buildings are frequent locations for film shoots and utility work. Look out for temporary paper signs taped to lampposts indicating "No Parking Temporary" for Wednesday, Nov 18.

### Summary Strategy for 10:00 AM on Nov 18, 2026:
1.  **Arrive 20 minutes early (9:40 AM).**

2.  **First pass:** Drive up **Claremont Ave** (from 116th to 120th), then loop onto **Riverside Drive**. If you find a free spot here, take it immediately.

3.  **Second pass (Meters):** If side streets are full, head to **Broadway** or **Amsterdam Ave**. Spaces open up frequently on these avenues as people run quick errands.

4.  **Have a backup plan:** If you haven't found street parking by 10:00 AM, use one of the local parking garages (such as the GGMC Garage on 112th St between Broadway and Amsterdam, or the Riverside Church Garage on 120th St) to avoid being late for your noon deadline.

#### CurbWise — full recorded answer
### 1. Direct Answer
Actionable street-parking investigation leads exist near Columbia University for Wednesday, November 18, 2026, from 10:00 AM to noon. Their legal status is classified as **INSUFFICIENT_EVIDENCE**, meaning legality could not be fully verified from available digital records alone, not that legal parking does not exist. These leads represent the best priority areas to search, though a promising lead does not constitute a final determination or permission to park.

### 2. Best Street Sides to Investigate
The following street sides are the top priority candidates for parking during your requested time:

*   **Broadway (East Side, from W 114th St to W 120th St):** This street side is classified as metered parking. The passenger parking meter schedule operates Monday–Friday from 8:00 AM to 10:00 PM with a 6-hour maximum stay, covering your entire parking window. Early morning street cleaning (7:30 AM–8:00 AM) ends prior to your arrival.

    *   **On-site check:** Confirm the exact curb-zone boundaries from the physical sign arrows on-site.

*   **Broadway (West Side, from W 120th St to W 116th St):** This street side is classified as metered parking. The passenger parking meter schedule operates Monday–Friday from 9:00 AM to 10:00 PM with a 6-hour maximum stay, covering your entire window. Early morning street cleaning (8:30 AM–9:00 AM) ends prior to your arrival. Be aware that overlapping "No Standing" rules apply to portions of this block.

    *   **On-site check:** Verify the physical sign arrows, watch for adjacent bus stops, and check for any arrowed "No Standing" plates.

*   **Amsterdam Ave (West Side):** This street side is classified as metered parking. Active passenger meters operate Monday–Saturday from 8:30 AM to 7:00 PM with a 2-hour maximum stay, matching your stay duration. There are no mapped signs on this side of the street in the digital database.

    *   **On-site check:** Confirm on-site curb signs to ensure no active unmapped restrictions exist.

### 3. Restrictions to Watch
When parking in this area, you must closely monitor physical signage to avoid the following overlapping restrictions:

*   **No Standing Anytime:** Active on portions of Broadway (West Side, from W 120th St to W 116th St) under partial arrows, and Amsterdam Ave (East Side).

*   **Truck Loading Only:** Passenger vehicles are prohibited on segments of Broadway (East Side) from Monday–Friday, 7:00 AM–1:00 PM, and Broadway (West Side) from Monday–Friday, 8:00 AM–6:00 PM.

*   **Bus Stop Zones:** Bus stop "No Standing" zones are active along sections of both Broadway and Amsterdam Ave.

*   **No Parking Anytime:** Restricts specific zones on Amsterdam Ave (East Side) and West 118th Street (South Side).

### 4. What Remains Unverified
*   **Approximate Geometry:** Digital mapping data utilizes road-bed centerlines; the exact physical curb boundaries and specific parking spaces are not mapped.

*   **Curb Sign Span:** The precise coverage of posted signs (especially partial arrows or double arrows) cannot be verified remotely.

*   **Meter Point Data:** Meter records represent point-specific coordinates and do not guarantee coverage of the entire blockface.

*   **Real-time Exceptions:** Active construction closures, emergency restrictions, and physical temporary postings are not captured by digital records and must be checked on-site.

### 5. Evidence
*   **Broadway East Side Signs:** See NYC OpenData sign database `P-01810504` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)).

*   **Broadway West Side Signs:** See NYC OpenData sign database `P-01762026` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)).

*   **Amsterdam Ave East Side Signs:** See NYC OpenData sign database `P-01866191` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)) and `S-01295546` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)).

*   **West 118th Street Signs:** See NYC OpenData sign database `S-01373605` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)) and `S-01471913` ([Source Link](https://data.cityofnewyork.us/d/nfid-uabd)).

*   **Meter Schedules & Statuses:** See NYC OpenData active meter records `1218181`, `1218187`, `1218188`, `1218189`, `1218179`, `1218180`, and `1389715` ([Source Link](https://data.cityofnewyork.us/d/693u-uax6)).

CurbWise is a research aid. Lead recommendations are investigation priorities, not determinations of legality or availability. Physical signs and temporary restrictions take precedence.

</details>

## Run locally

You'll need Python, [`uv`](https://docs.astral.sh/uv/), and a Google Cloud project with Vertex AI access.

```bash
uv sync --frozen
gcloud auth application-default login
export VERTEXAI_PROJECT="your-project-id"
uv run app.py
```

Open `http://localhost:8000` to use the map.
