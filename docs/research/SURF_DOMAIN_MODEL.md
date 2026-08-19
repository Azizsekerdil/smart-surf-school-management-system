# Surf School Domain Model — Research Findings

**Project:** Smart Surf School Management System
**Stack context:** Python 3.11 / Django 5 / DRF / HTMX / Alpine / Tailwind, SQLite dev → PostgreSQL prod, Windows 11 native
**Research date:** 2026-08-15
**Purpose:** Ground the data model, the safety rule engine, and the AI Surf Score algorithm in real-world surf school practice rather than invented concepts.

---

## 0. Executive Summary — Numbers You Can Implement Today

| Constant | Value | Source |
|---|---|---|
| Max coach:student ratio, beginner/novice | **1:8** | Surfing England Surf School Scheme |
| Max coach:student ratio, upper improver/intermediate | **1:5** (coach must be L2+) | Surfing England |
| Recommended coach:student ratio, advanced | **1:4** | Surfing England |
| Hard wave-height ceiling for beginner lessons | **1.5 m** | Surfing England |
| Beginner recommended wave size | **< 1.5 ft whitewater**, chest-deep water | Academy of Surfing Instructors |
| Beginner wind ceiling | **< 20 km/h (≈10.8 kt)** | Academy of Surfing Instructors |
| Advanced wind ceiling | **< 35 km/h (≈19 kt)** | Academy of Surfing Instructors |
| Rescue board minimum | **8 ft × 22 in**, one per group, within **200 m** | Surfing England |
| First aid kit proximity | within **200 m** of water activity | Surfing England |
| Public liability insurance | up to **£5 m** | Surfing England |
| Incident report copy to student | within **72 hours** | Surfing England |
| ISA coaching certificate renewal | **every 2 years**, $125 USD + 1 CPD credit | ISA |
| ISA/ILS Water Safety accreditation refresh | **every 2 years** | ISA |
| RLSS beach lifeguard (NVBLQ) validity | **2 years** | RLSS UK |
| HSE First Aid at Work validity | **3 years** (annual refresher recommended) | HSE / UK providers |
| Safeguarding + DBS refresh (UK sport norm) | **3 years** | UK NGB practice |
| Beginner board volume | **≈ 1.0 × body weight (kg) → litres** on a foamie | Global Surf Industries |
| Advanced board volume | **≈ 0.35–0.40 × body weight (kg)** | Volume coefficient convention |
| Good swell period threshold | **≥ 10 s** good, **≥ 12 s** excellent | Surf forecasting convention |
| Red flag | **No lesson may run** unless lifeguards assign a dedicated area | Surfing England |

**Non-negotiable design conclusion:** ratios, wave height, wind, flag state and instructor certification currency are **safety constraints, not preferences**. They belong in a validation layer that can *block* a booking or a session start, not in a scoring function that merely lowers a number.

---

## 1. How Real Surf Schools Operate — Software Landscape and Implied Data Model

### 1.1 Generic activity-booking platforms

These dominate the market and define the "table stakes" feature set. Verified 2026 positioning:

| Platform | Commercial model (2026) | Notable domain features |
|---|---|---|
| **Rezdy** | from ~$49/month | Channel manager / distribution to agents & OTAs |
| **Checkfront** | ~$99–125/month + ~3% booking fee | 100+ features, **resource management**, built-in **waivers**, OTA connections, reseller network |
| **Peek Pro** | no monthly fee, ~3–6% booking fee | AI dynamic pricing, abandoned-cart recovery, offline check-in, **bulk guide assignment**, WhatsApp confirmations |
| **Xola** | no subscription, **2.39% + $0.30** per booking | Transaction-fee model |
| **Bookeo** | from ~$14.95/month | **Multi-resource booking** (seats/cabins/vessels across schedules) |

### 1.2 Surf/watersports-specific platforms

These are the ones that reveal *surf-specific* entities:

- **WaveRez** — class scheduling, instructor assignment, **student skill-level tracking**, group lesson management, wetsuit & board rental.
- **Bloowatch** — bookings, staff, boats, **gear**, waivers, payments, **manifests**, reports; explicitly advertises **"tide-aware scheduling to plan the right windows automatically"**, drag-and-drop group reassignment, deposits to reduce no-shows, and **mandatory custom checkout questions (level, preferences, notes, sizes)**.
- **Yoplanning**, **Lueira**, **Anolla**, **Viking Bookings**, **Roverd** — instructor scheduling + board/wetsuit rental inventory.

### 1.3 What this tells us about the entity model

The commercially-proven core entities are:

```
Customer ──< Booking ──< BookingLine ──> Product (Lesson / Camp / Rental)
                │
                └──> Waiver (signed, versioned)

Session (a scheduled, dated instance at a Beach)
   ├──< SessionGroup (the actual coached group — this is where ratio is enforced)
   │        ├──> Instructor (assigned)
   │        ├──< Participant (Customer @ a SkillLevel)
   │        └──> EquipmentAssignment (Board, Wetsuit, per participant)
   └──> ConditionsSnapshot (swell, wind, tide, flag, water temp — frozen at time of session)

Instructor ──< Certification (type, number, issued, expires, evidence file)
Equipment  ──< MaintenanceRecord (Ding → Repair → back in service)
Incident   ──> Session, Participant, ConditionsSnapshot
```

Two things every generic platform gets wrong for surf, and which are our differentiators:

1. **Capacity is not a single number.** A session's capacity is `sum(group.max_size)` where each group's max size derives from *the group's skill level* and *the assigned instructor's qualification level*. A 1:8 beginner group and a 1:4 advanced group cannot be modelled by one "max participants" field.
2. **Availability is tide- and condition-dependent.** Bloowatch's "tide-aware scheduling" proves the market wants this. Our AI Surf Score is the natural extension.

> ### RECOMMENDATION
> Model the domain with a **Session → SessionGroup → Participant** three-level hierarchy, never a flat `Session.max_participants`. Enforce ratio at the `SessionGroup` level via a Django `clean()` + a DB `CheckConstraint` where possible, and re-validate on every participant add. Copy the proven generic entities (Customer, Booking, Waiver, Product, Manifest, Deposit) rather than reinventing them, and add the four surf-specific ones the generic platforms lack: `ConditionsSnapshot`, `Certification`, `EquipmentAssignment`, `Incident`. Store a **frozen `ConditionsSnapshot` per session** — never recompute historical conditions, because the snapshot is legal evidence if an incident is investigated.

---

## 2. Instructor Certification Schemes and Expiry Tracking

### 2.1 The real certification stack

A working UK/EU surf coach typically holds **four to five separate, independently-expiring credentials**. This is the single most important insight for the data model: certification is **not** a field on the instructor, it is a **collection of records each with its own expiry**.

#### A. Coaching award (the teaching qualification)

**ISA (International Surfing Association)** — the global scheme:

- **ISA Surf Level 1 Instructor**
  - Prerequisite: **ISA Introduction to Surf Instructing E-Learning Course** (a newer requirement).
  - Minimum age **18 to instruct unsupervised**; in the UK the award can be held from **16**, but a Level 1 coach under 18 **must always work under supervision** of a Level 2 or higher / experienced coach (Surfing England rule).
  - Surfing ability entry standard: paddle out in **shoulder-high surf**, navigate the line-up, catch waves and pop cleanly to the feet, take off both directions and perform a clear change of direction on the wave face.
  - Course length: typically **2 days** (~£350 in the UK).
  - Assessment components: **venue analysis, lesson planning, lesson delivery, surf skills**.
  - Post-course: **20 hours** of observed/supervised teaching internship certified by a qualified ISA supervisory coach — completable within **one year**.
  - Must hold a valid **open-water lifeguard / water safety card** — also completable within one year of the course.

- **ISA Surf Level 2 Coach**
  - Must have passed **ISA Level 1** first.
  - Minimum **12 months** coaching experience at Level 1.
  - Practical coaching workbook documenting **40 hours** of coaching.
  - Course length typically **3 days** (~£450 in the UK).
  - Aimed at coaches working with **novice → intermediate** surfers; widely regarded by Surfing England as the qualification for **head coach** appointments.

**Surfing England** runs a parallel/aligned ladder: **Surfing England Level 1, 2, 3, 4**, treated as interchangeable with ISA Level 1/2 in the Surf School Scheme's descriptors.

#### B. Water safety / rescue award (the thing that expires fastest)

- ISA requires a **current, recognised water safety/rescue award** to be *held and maintained* — an ISA certificate without a current rescue award is not valid for practice.
- The **ISA/ILS Water Safety Accreditation must be refreshed every two years.**
- UK equivalents accepted in practice: **SLSGB Surf Coach Safety & Rescue Award**, **RLSS UK beach lifeguard**, **NARS Beach Lifeguard**.
- The **RLSS National Vocational Beach Lifeguard Qualification (NVBLQ) is valid for two years** from date of successful completion.

#### C. First aid

- **HSE First Aid at Work (FAW): valid 3 years.** Renewed via a 2-day requalification course **before** expiry.
- **Emergency First Aid at Work (EFAW): valid 3 years**, renewed by retaking the 1-day course.
- **Critical rule to encode:** if the certificate has lapsed by **more than one month**, HSE recommends the **full 3-day course**, not the 2-day requalification. So a lapsed cert is materially more expensive than a renewed one — the system must warn early.
- HSE **recommends an annual refresher** (usually half-day, focused on CPR/AED). Recommended, **not legally required**.

#### D. Safeguarding and DBS

- Surfing England has required **DBS checks** as a prerequisite for approval **since 2010**. "If the coach does not hold Surfing England coaches membership, then they are not fully qualified."
- Responsibility for DBS checks sits explicitly with the **surf school owner** when working with children or vulnerable adults.
- UK sport NGB norm: **DBS renewed every 3 years**; **safeguarding training certificates valid 3 years**. The **DBS Update Service** allows continuous monitoring instead of re-checking.

#### E. Governing body membership

- Surfing England **surf coach membership (name + number)** is required annually for every coach employed at an approved school. Membership lapse = coach is *not fully qualified*, regardless of awards held.

### 2.2 How expiry is actually tracked — the ISA status model

ISA operates a **three-state lifecycle** that we should copy directly, because it is more nuanced (and more useful) than a boolean:

| Status | Meaning |
|---|---|
| **Current** | All renewal requirements complete, before expiry date. Valid to practise. |
| **Not Current** | Renewal requirements not completed before the expiry date. **Grace period of up to 3 years.** Not valid to practise, but renewable. |
| **Expired** | Has been "Not Current" for more than **3 years**. No longer renewable — must complete the **ISA Refresher Training E-Learning Course** to re-qualify. |

ISA renewal requirements (all three must be met):
1. Pay **$125 USD** renewal fee.
2. Upload a **valid and current Surf Rescue / Water Safety certification**.
3. Complete **at least 1 Continuing Professional Development (CPD) credit** via ISA Source.

> ### RECOMMENDATION
> Implement a dedicated `Certification` model — **never** boolean fields like `is_lifeguard_qualified` on `Instructor`:
>
> ```python
> class CertificationType(models.TextChoices):
>     ISA_L1 = "ISA_L1", "ISA Surf Level 1 Instructor"
>     ISA_L2 = "ISA_L2", "ISA Surf Level 2 Coach"
>     SE_L1  = "SE_L1",  "Surfing England Level 1"
>     SE_L2  = "SE_L2",  "Surfing England Level 2"
>     SE_L3  = "SE_L3",  "Surfing England Level 3"
>     SE_L4  = "SE_L4",  "Surfing England Level 4"
>     WATER_SAFETY = "WATER_SAFETY", "ISA/ILS Water Safety Accreditation"
>     BEACH_LIFEGUARD = "BEACH_LIFEGUARD", "Beach Lifeguard (RLSS/NARS/SLSGB)"
>     FIRST_AID_FAW = "FIRST_AID_FAW", "HSE First Aid at Work"
>     FIRST_AID_EFAW = "FIRST_AID_EFAW", "Emergency First Aid at Work"
>     SAFEGUARDING = "SAFEGUARDING", "Safeguarding Children"
>     DBS = "DBS", "DBS Enhanced Disclosure"
>     NGB_MEMBERSHIP = "NGB_MEMBERSHIP", "Governing Body Coach Membership"
>
> class CertificationStatus(models.TextChoices):
>     CURRENT = "CURRENT"
>     NOT_CURRENT = "NOT_CURRENT"   # expired but inside renewal grace window
>     EXPIRED = "EXPIRED"           # past grace window, requires re-qualification
> ```
>
> Default validity periods to seed (days): `WATER_SAFETY` 730, `BEACH_LIFEGUARD` 730, `ISA_L1`/`ISA_L2` 730, `FIRST_AID_FAW`/`FIRST_AID_EFAW` 1095, `SAFEGUARDING` 1095, `DBS` 1095, `NGB_MEMBERSHIP` 365 (calendar year). Grace window for coaching awards: **1095 days**.
>
> Store `certificate_number`, `issuing_body`, `issued_on`, `expires_on`, `evidence_file` (upload), and a computed `status`. Add a nightly Celery beat task (or a Windows Task Scheduler → `manage.py` command if Celery is deferred) that emails alerts at **T-90, T-60, T-30, T-7 days** and on expiry. **T-30 is the critical one for first aid** because of the one-month HSE cliff.
>
> Add a hard gate: `Instructor.can_be_assigned(skill_level)` returns False if **any** of `WATER_SAFETY`/`BEACH_LIFEGUARD`, first aid, safeguarding, DBS, or the required coaching award is not `CURRENT`. Assigning an instructor with a lapsed rescue award to a session must be **blocked**, not warned — this is the single highest-liability failure mode for a surf school.

---

## 3. Lesson Taxonomy, Durations, and Instructor:Student Ratios (SAFETY-CRITICAL)

### 3.1 The authoritative ratio table

Source: **Surfing England Surf School Scheme, Regulations and Application** (the scheme document schools must comply with to be an approved English Surf School). Direct quotations:

**Novice (beginner & low-ability improver) groups:**
> "The 1:8 coach / surfer ratio will be the maximum ratio operated. Also conditions for this ratio must be 'appropriate' and risks assessed as 'tolerable' for the group. A reduced coach/pupil maximum ratio is recommended for changing group conditions e.g. smaller children, more severe surfing conditions such as: crowded breaks; bigger surf; lateral rips (long shore drift); strong winds etc."

> "Any Level 1 coach under the age of 18 must always work under the supervision of a Level 2 or higher or experienced coach."

**Non-novice groups:**
> "The upper level improver / intermediate coaching will have a maximum teaching ratio of **1:5** with all coaches qualified to a minimum Surfing England Level 2 or ISA Level 2."

> "The recommended teaching ratio for advanced groups is **1:4**."

**Mixed-ability groups — the subtle rule most systems get wrong:**
> "If there is a group of mixed-ability surfers and one instructor is out-the-back with the more competent surfers, improver novices or other higher abilities in deeper water more than chest depth, then the less able group of beginners still in shallow water will be considered **another group**. With mixed-ability groups, **no group must be operated at more than the allowed ratio and each group must have its own designated rescue-demo board**."

Additional operational rule: coaches **must not free surf** during lessons (a "coach's demonstration" of a lesson objective is permitted and is distinct from free surfing).

Surfing England has separately confirmed the return to **1:8** as the maximum group lesson ratio, with organised group activity sessions permitted up to **30 people including the coach** in total. Where a colleague holding a relevant surf lifesaving award assists, schools operate **2 staff : 8 students** — this is a *supervision enhancement*, **not** a licence to raise the ratio to 1:16.

### 3.2 Consolidated ratio matrix (implement this)

| Level code | Level name | Max ratio | Min instructor qualification | Water depth | Notes |
|---|---|---|---|---|---|
| `FIRST_TIMER` | Absolute first-timer | **1:8** | ISA/SE L1 (supervised if <18) | Waist depth | Soft-top mandatory |
| `BEGINNER` | Beginner | **1:8** | ISA/SE L1 | Waist–chest | Soft-top mandatory |
| `IMPROVER_LOW` | Low-ability improver | **1:8** | ISA/SE L1 | Chest | Soft-top mandatory |
| `IMPROVER_HIGH` | Upper improver | **1:5** | ISA/SE **L2** | Beyond chest | Hardboard permitted |
| `INTERMEDIATE` | Intermediate | **1:5** | ISA/SE **L2** | Line-up | Hardboard |
| `ADVANCED` | Advanced | **1:4** | ISA/SE **L2**+ (L3/L4 preferred) | Line-up | Hardboard |

**Age-based reduction factors** (industry practice — Surfing England mandates "reduced ratio" for "smaller children" without a number, so we set a defensible house rule):

| Age band | Applied max ratio | Basis |
|---|---|---|
| Under 8 | **1:4** (schools commonly go to 1:1–1:2 privately) | "Micro-groms" clubs 5–8; some schools use 1:1 for under-8s |
| 8–11 | **1:6** | Common junior club practice (up to 6 in mixed groups) |
| 12–17 | **1:8** (standard) | Standard novice ratio |
| 18+ | **1:8** (standard) | Standard novice ratio |

**Minimum age:** most schools set **5–7 years**. The real gate is not age but water competence: children should be **comfortable in water and able to swim ~25 m unassisted**.

### 3.3 Lesson taxonomy and durations

| Product type | Ratio | Typical duration | Typical positioning |
|---|---|---|---|
| **Group lesson** | up to 1:8 (novice) | **2 hours** (1.5 h common alternative) | Entry price point; solo travellers |
| **Semi-private** | **1:2 – 1:3** | 90 min – 2 h | Couples / small friend groups; best value tier |
| **Private (1:1)** | **1:1** | 60–120 min | Fastest progression; premium price |
| **Kids' group / junior club** | 1:4 – 1:6 by age | 90 min – 2 h | Term-time clubs, holiday camps |
| **Improver / intermediate coaching** | 1:5 | 2 h | Requires L2 coach |
| **Advanced / performance** | 1:4 | 2–3 h | Video analysis typical |
| **Multi-day course** | as per level | 3–5 consecutive days | Progression pathway |
| **Surf camp** | as per level | 7 nights / 8 days | See §11 |
| **Rental only** | n/a | Half-day / full-day / weekly | Board + wetsuit |

Standard inclusions in the price: **board, wetsuit/rashguard, and use of lockers/showers**.

> ### RECOMMENDATION
> Create a `SkillLevel` model (ordered, with `code`, `display_name`, `sort_order`, `max_ratio`, `min_instructor_cert_level`, `requires_softtop`, `max_wave_height_m`) and seed it with the six levels above. Store ratio as **data, not code**, so a school in another jurisdiction can adjust it — but ship the Surfing England values as defaults and log any override.
>
> Compute the effective ratio as:
> `effective_max = min(level.max_ratio, age_band_ratio(youngest_participant), condition_modifier)` where `condition_modifier` reduces the ratio in the documented adverse cases (small children, crowded break, bigger surf, lateral rip, strong wind). Implement `condition_modifier` as **−2 students** when any two adverse factors are flagged, **−4** when three or more. This operationalises Surfing England's "reduced ratio is recommended" language into something enforceable.
>
> Implement the **mixed-ability rule** literally: if a `SessionGroup` contains participants whose levels span the chest-depth boundary (`IMPROVER_LOW` and below vs `IMPROVER_HIGH` and above), the system must **force a split into two `SessionGroup` records**, each with its own instructor and its own `rescue_board` assignment. Do not allow a single group to straddle that boundary.
>
> Make `Product` (lesson type) separate from `SkillLevel`. A "2-hour group lesson" product can be offered at several levels; the ratio comes from the level, the price and duration come from the product.

---

## 4. Surfboard Sizing — Volume and Length

### 4.1 The volume formula

The industry-standard formula is:

```
recommended_volume_litres = rider_weight_kg × ability_coefficient
```

**There are two competing coefficient conventions in the literature and you must handle both**, because they answer different questions:

**Convention A — "performance shortboard" coefficients** (what board designers and volume calculators use, for surfers choosing a *hardboard*):

| Ability | Coefficient (L per kg) |
|---|---|
| Beginner | **0.62** (range 0.50–0.70) |
| Intermediate | **0.47** (range 0.38–0.50) |
| Advanced | **0.39** (range 0.32–0.40) |
| Expert / pro | **0.35** |

**Convention B — "learn-to-surf" coefficients** (what surf schools and soft-top makers use, for absolute beginners on foamies). Global Surf Industries states it plainly:

> "a beginner should be riding the equivalent of **100% of their body weight** in volume" … "an experienced surfer can ride a board that's around **40%** of their body weight" … "A pro might ride **30%** of their body weight."

Worked example given: an **80 kg (176 lb) beginner → ~80 litres**.

**Reconciling the two:** Convention B's "beginner" means *absolute first-timer on a soft-top in whitewater*. Convention A's "beginner" means *someone who can already stand up, moving onto a hardboard*. They are different personas, and a surf school needs **both**.

### 4.2 Unified coefficient table (recommended for implementation)

| Our `SkillLevel` | Coefficient (L/kg) | Board type | Rationale |
|---|---|---|---|
| `FIRST_TIMER` | **1.00** | Soft-top | Global Surf Industries "100% of body weight" |
| `BEGINNER` | **0.85** | Soft-top | Interpolated; still needs high float |
| `IMPROVER_LOW` | **0.70** | Soft-top | Top of Convention A beginner range |
| `IMPROVER_HIGH` | **0.55** | Soft-top or hardboard | Between beginner and intermediate |
| `INTERMEDIATE` | **0.47** | Hardboard | Convention A intermediate |
| `ADVANCED` | **0.39** | Hardboard | Convention A advanced |
| `EXPERT` | **0.35** | Hardboard | Convention A expert |

**Fitness / age adjustment:** apply a multiplier. Sources consistently note that "a fit surfer who paddles well can ride a little less volume, while someone less fit, older, or returning after a break benefits from extra float."

| Modifier | Multiplier |
|---|---|
| Very fit, surfs weekly | **× 0.95** |
| Average (default) | **× 1.00** |
| Low fitness / age 50+ / returning after long break | **× 1.10** |

### 4.3 Board length table for beginners (soft-tops)

Volume alone is insufficient for a rental fleet, because schools buy boards by *length*. Verified guidance:

| Rider weight | Recommended soft-top length | Volume band |
|---|---|---|
| Under 65 kg | **7'0"** | ~45–55 L |
| Up to 75 kg | **7'0" – 7'6"** | **50–60 L** |
| 75 – 85 kg | **7'6" – 8'0"** | **60–75 L** |
| Over 85 kg | **8'0" – 9'0"** | 75–95 L |
| General adult beginner default | **8'0" – 9'6"** | Extra length = stability + wave-catching |

Note the tension: the length table (50–60 L for a 75 kg rider = 0.73 L/kg) is *less* volume than the 1.0 L/kg rule. This is because real soft-tops in those lengths simply are that volume, and the length itself contributes stability that raw volume does not capture. **Resolve in favour of the length table for the rental fleet**, and use volume as a secondary sanity check.

**Regulatory constraint (Surfing England, mandatory):**
> "Soft construction surfboards soft deck and bottom with 'rubberized/plastic' fins must be used for absolute beginners and 'low ability improvers'."

> ### RECOMMENDATION
> Store `volume_litres`, `length_inches`, `width_inches`, `thickness_inches`, `construction` (`SOFT_TOP` / `EPOXY` / `PU_POLY`), and `fin_type` on the `Board` model. Length in **inches as an integer** (96 = 8'0") so you can sort and range-query; render as feet/inches in the UI.
>
> Implement `recommend_board(rider_weight_kg, skill_level, fitness_modifier)` returning a **ranked queryset** of available boards, not a single number: score each in-service board by `abs(board.volume - target_volume)` with a **hard filter** that `skill_level in (FIRST_TIMER, BEGINNER, IMPROVER_LOW)` ⟹ `construction == SOFT_TOP and fin_type in (RUBBER, PLASTIC)`. That hard filter is a **legal compliance rule**, not a preference — surface it in the UI as such.
>
> Store the coefficient table in a `BoardSizingRule` model so it is tunable per school without a migration. Log which board was actually assigned versus recommended — that data trains a better recommender later and is exactly the kind of feedback loop the "Smart" in Smart Surf School should exploit.

---

## 5. Wetsuit Thickness by Water Temperature, and Sizing

### 5.1 Reading the notation

Thickness is written `A/B` where **A = torso panel thickness in mm** and **B = arms and legs thickness in mm**. Thicker core = warmth; thinner limbs = mobility. A `4/3` is 4 mm torso, 3 mm limbs.

### 5.2 The temperature table

Two independent manufacturer sources (Rip Curl and O'Neill EU) agree closely. Consolidated, conservative table (favouring the warmer recommendation at each boundary, which is correct for a surf school because students are **stationary and cold more often than instructors**):

| Water temp (°C) | Water temp (°F) | Thickness | Suit style | Boots | Gloves | Hood |
|---|---|---|---|---|---|---|
| **≥ 24** | ≥ 75 | 0–1 mm | Boardshorts / rashguard / lycra | – | – | – |
| **21 – 24** | 70 – 75 | **2 mm** | Shorty / spring suit | – | – | – |
| **18 – 21** | 65 – 70 | **3/2 mm** | Full suit (or shorty) | optional | – | – |
| **15 – 18** | 60 – 65 | **3/2 mm** | Full suit | optional | – | – |
| **13 – 15** | 55 – 60 | **4/3 mm** | Full suit | **recommended** | optional | – |
| **9 – 13** | 48 – 55 | **4/3 – 5/4 mm** | Full suit | **yes** | **yes** | optional |
| **7 – 9** | 45 – 48 | **5/4 – 6/5 mm** | Hooded full suit | **yes** | **yes** | **yes** |
| **4 – 7** | 39 – 45 | **6/5 – 7 mm** | Hooded full suit | **yes** | **yes** | **yes** |
| **≤ 3** | ≤ 38 | **7 mm+** | Hooded full suit | **essential** | **essential** | **essential** |

**Accessory thresholds worth encoding as explicit rules:**
- **Hood becomes essential below 10 °C (50 °F)** — heat loss from an uncovered head is significant.
- **Gloves transition from optional to necessary below ~13 °C (55 °F)** for sessions exceeding one hour.

**Critical caveat all sources state:** water temperature is not the only input. **Wind chill, session length, activity intensity, and individual cold tolerance** all matter. Water steals heat from the body roughly **24× faster than air**.

**Surf-school-specific adjustment:** a beginner student spends most of the lesson **standing still in waist-deep water being talked at**, while the instructor is moving. Students get colder than the table predicts.

**Regulatory constraint (Surfing England, mandatory):**
> "Appropriate full (steamer) wetsuits in good repair and designed for the time of year lessons are being conducted."
> "Neoprene accessories (i.e. hoods, boots, gloves etc) to be **available for customers** who may require them due to sea & wind temperatures or medical reactions."

Note the wording: accessories must be **available**, not merely recommended. That is an inventory obligation.

### 5.3 Wetsuit sizing

- Sizing keys off **height, weight, chest, and waist**.
- Chest: measure around the fullest part of the chest, standing naturally. Waist: around the natural waistline, just above the navel.
- **Tall variants** exist: `ST`, `MT`, `LT` — same chest as the standard size but **longer torso and limbs**. Essential for a rental fleet because a badly-fitting suit flushes and the student gets cold and quits.
- When between sizes, **prioritise chest and height** — these determine whether the suit seals at neck and wrists.
- Fit should be "second skin": **no loose folds, gaps, or air pockets**. It feels tight on land and loosens slightly when wet.
- **Sizing is NOT standardised between brands** — a medium in one brand fits like a large in another, and cuts vary between a brand's budget and premium lines.

> ### RECOMMENDATION
> Store on `Wetsuit`: `brand`, `model`, `size_label` (free text: `MT`, `L`, `10`, `JS`), `thickness_torso_mm`, `thickness_limb_mm`, `style` (`SHORTY`/`FULL`/`HOODED_FULL`), `is_tall_variant`, `condition`, `status`.
>
> **Because sizing is not standardised, do NOT build a global size chart.** Instead store a per-brand `WetsuitSizeChart` mapping `(brand, size_label) → (height_cm_min/max, weight_kg_min/max, chest_cm_min/max)`. Seed it from the manufacturer chart for each brand the school actually buys. This is the only correct model and it is a genuine differentiator over generic booking tools.
>
> Implement `recommend_wetsuit(water_temp_c, session_duration_min, participant)` returning `(thickness, style, [required_accessories])` from the table above, then **apply a school-safety adjustment: for `FIRST_TIMER`/`BEGINNER` participants, step one band warmer** (they are stationary). Return accessories as a *hard requirement list* below 10 °C so the check-in screen can block a student from entering the water without a hood.
>
> Add a `water_temp_c` field to `ConditionsSnapshot` and drive the whole thing off it. Log the actual suit issued so you can detect fleet gaps ("we turned away 6 tall students in July").

---

## 6. Surfboard Ding and Damage Taxonomy, and Repair Workflow

### 6.1 Damage taxonomy

| Code | Damage type | Description | Water ingress risk | Typical severity |
|---|---|---|---|---|
| `PRESSURE_DING` | Pressure ding | Indentation in the glass **without** cracking. Caused by foot/knee pressure on the deck. Cosmetic individually, but excessive pressure dings lead to cracking and eventual water damage. | Low (until it cracks) | Minor |
| `CRACK` | Impact crack / stress crack | Cracks along rails, deck or bottom from stress or impact (wipeouts, collisions). | **Yes** | Moderate |
| `OPEN_DING` | Open ding / puncture | Glass broken through, **foam exposed**. | **High — do not surf** | Major |
| `RAIL_DING` | Cracked or crushed rail | Rail damage from board-to-board or board-to-rock contact. | Yes | Moderate |
| `NOSE_DAMAGE` | Nose damage | Broken/crushed nose. Common on beginner boards. | Yes | Moderate–Major |
| `TAIL_DAMAGE` | Tail damage | Smashed tail, open foam. | Yes | Moderate–Major |
| `DELAMINATION` | Delamination | Fibreglass layer **separates from the foam core**, usually from prolonged heat or pressure exposure. Common on epoxy boards left in hot cars or direct sun. | Yes | Major |
| `FIN_BOX` | Fin box damage | Damaged/loose fin box from collision or improper fin installation. Systems: **FCS, FCS II, Futures, single-fin box**. | Yes | Major |
| `LEASH_PLUG` | Leash plug pull-out | Plug torn from the deck. | Yes | **Major — safety-critical** |
| `SNAP` | Snapped / buckled | Board broken or buckled through. | n/a | **Write-off** |
| `WATERLOGGED` | Waterlogged | Foam saturated through unrepaired damage. Board is heavy and structurally compromised. | n/a | **Write-off / major** |

### 6.2 The resin-matching rule (encode this — it prevents destroying boards)

> **Always match the resin to the board: polyester resin on a PU board, epoxy resin on an epoxy board. Using polyester resin on an epoxy board will cause delamination.**

Practical corollary widely applied in repair shops: **epoxy resin is safe on both PU and epoxy boards; polyester is safe only on PU.** So when in doubt, epoxy.

Soft-tops are a third case — most school fleets treat cracked soft-tops as consumables rather than repairing them properly, because the foam skin does not accept standard glassing.

### 6.3 Repair workflow

Standard DIY/shop process from the sources: **clean with acetone → sand lightly around the ding to key the surface → apply resin mixed per manufacturer instructions → cure → sand → finish**. For delamination covering **more than a few square inches**, a professional injects resin and applies **vacuum pressure** to bond across the whole affected area.

Operational workflow for a school fleet:

```
REPORTED ──> TRIAGED ──> QUARANTINED ──> DRYING ──> IN_REPAIR ──> CURING ──> QA_CHECK ──> IN_SERVICE
     │            │                                                              │
     │            └──> (severity = WRITE_OFF) ────────────────────────────> RETIRED
     └──> (severity = COSMETIC) ──────────────────────────────────────────> IN_SERVICE (monitor)
```

**Drying is a mandatory, time-boxed stage.** Sealing water inside foam guarantees the repair fails and the board rots. A board with water ingress must be out of service for a fixed drying period before repair begins — this is exactly the kind of rule an operations system should enforce, because under pressure on a busy August morning, staff will skip it.

> ### RECOMMENDATION
> Model `Equipment` (abstract or concrete base) with subclasses/proxies `Board` and `Wetsuit`, plus a `DamageReport` and a `RepairJob`.
>
> `DamageReport`: `equipment`, `damage_type` (choices above), `severity` (`COSMETIC` / `MINOR` / `MODERATE` / `MAJOR` / `WRITE_OFF`), `location_on_board` (`DECK`/`BOTTOM`/`RAIL_L`/`RAIL_R`/`NOSE`/`TAIL`/`FIN_BOX`/`LEASH_PLUG`), `water_ingress` (bool), `reported_by` (instructor), `reported_at`, `session` (FK — **which lesson did this happen in**), `photo`.
>
> Implement `Equipment.status` as a state machine matching the workflow above. Add the hard rule: **`water_ingress=True` ⟹ minimum 48-hour `DRYING` stage before `IN_REPAIR`** (tune per school), enforced by refusing the state transition. Add `resin_type_required` as a **computed property** from `board.construction` (`PU_POLY → POLYESTER or EPOXY`; `EPOXY → EPOXY ONLY`) and display it prominently on the repair job card — this single field prevents an expensive, irreversible mistake.
>
> Block any board not `IN_SERVICE` from appearing in `EquipmentAssignment` availability. Link `DamageReport.session` so you can answer "which instructor/lesson type breaks the most boards" — a real cost-control question every surf school owner has.

---

## 7. Wind — Beaufort Scale and Direction Relative to the Beach

### 7.1 Beaufort scale (NOAA/WPC, authoritative)

| Force | Name | Knots | mph | Sea wave height | Sea condition |
|---|---|---|---|---|---|
| 0 | Calm | 0 | 0 | 0 m | Like a mirror |
| 1 | Light air | 1–3 | 1–3 | 0.1 m | Ripples, no foam crests |
| 2 | Light breeze | 4–6 | 4–7 | 0.2–0.3 m | Small wavelets, not breaking |
| 3 | Gentle breeze | 7–10 | 8–12 | 0.6–1 m | Small wavelets, crests begin to break |
| 4 | Moderate breeze | 11–16 | 13–18 | 1–1.5 m | Small waves lengthening, numerous whitecaps |
| 5 | Fresh breeze | 17–21 | 19–24 | 2–2.5 m | Moderate waves, many whitecaps |
| 6 | Strong breeze | 22–27 | 25–31 | 3–4 m | Larger waves, whitecaps everywhere |
| 7 | Near gale | 28–33 | 32–38 | 4–5.5 m | Sea heaps up, white foam blown around |
| 8 | Gale | 34–40 | 39–46 | 5.5–7.5 m | Crests break into spindrift |
| 9 | Strong gale | 41–47 | 47–54 | 7–10 m | High waves, sea rolls, reduced visibility |
| 10 | Storm | 48–55 | 55–63 | 9–12.5 m | Very high waves, overhanging crests |
| 11 | Violent storm | 56–63 | 64–73 | 11.5–16 m | Exceptionally high waves |
| 12 | Hurricane | > 63 | > 73 | 16 m+ | Sea completely white, excessive foam |

### 7.2 Wind direction relative to the beach — why it matters

Wind direction is defined **relative to the shoreline normal**, so it must be computed from the beach's own orientation, not stated absolutely.

| Class | Definition | Effect on wave quality |
|---|---|---|
| **Offshore** | Blows from land → sea | **Best.** Holds up the wave face, creates clean, hollow, well-shaped waves and barrels; delays the break and holds it open longer. |
| **Cross-offshore** | Diagonal, land-ish | Considered **excellent** by many surfers — near-offshore benefits with less spray in the eyes. |
| **Cross-shore** | Parallel to the coastline | **Mixed.** Effect falls between offshore and onshore depending on exact angle. Creates a bumpy, disorganised face and can drive longshore drift. |
| **Cross-onshore** | Diagonal, sea-ish | Degraded, choppy. |
| **Onshore** | Blows from sea → land | **Worst.** Flattens and crumbles waves, creates chop and closeouts. A mild onshore (**under 10 kt**) on a strong, long-period swell may still be rideable. |

### 7.3 Numeric wind thresholds for surf quality

- **Ideal:** light offshore, **5–15 knots** (9–28 km/h). This is the classic best case.
- **Offshore acceptable up to:** **< 20 knots** (37 km/h / 23 mph). Beyond this, strong offshore makes take-off difficult and holds waves up until they close out.
- **Onshore / cross-shore acceptable up to:** **< 10 knots** (18 km/h / 11 mph) to avoid excessive chop.
- **Preferred Beaufort band:** **Force 2–4** for clean conditions.

### 7.4 The safety inversion — critical for a surf school

**Offshore wind is best for wave quality but is a hazard for beginners and for anything inflatable.** The ILS/RNLI **orange windsock** exists specifically to signal **offshore wind conditions and that inflatables must not be used**. Offshore wind blows people and floating objects **away from shore**.

Surfing England explicitly lists **"strong winds"** among the conditions requiring a **reduced coach:student ratio**.

Academy of Surfing Instructors' recreational standards give hard wind ceilings by level:
- **Beginner: wind under 20 km/h** (≈10.8 kt / Beaufort 3)
- **Intermediate: wind under 20 km/h** (≈10.8 kt / Beaufort 3)
- **Advanced: wind under 35 km/h** (≈19 kt / Beaufort 5)

> ### RECOMMENDATION
> Store `beach.shore_normal_bearing_deg` (the compass bearing of the outward-facing perpendicular to the shoreline, 0–359) on the `Beach` model. Compute wind class from the meteorological wind-**from** direction:
>
> ```python
> def wind_class(wind_from_deg: float, shore_normal_deg: float) -> str:
>     # shore_normal points from land out to sea.
>     # Offshore wind comes FROM the land, i.e. from the opposite of shore_normal.
>     offshore_from = (shore_normal_deg + 180) % 360
>     delta = abs((wind_from_deg - offshore_from + 180) % 360 - 180)
>     if delta <= 45:   return "OFFSHORE"
>     if delta <= 75:   return "CROSS_OFFSHORE"
>     if delta <= 105:  return "CROSS_SHORE"
>     if delta <= 135:  return "CROSS_ONSHORE"
>     return "ONSHORE"
> ```
>
> Store wind speed canonically in **knots** (marine/surf standard; convert on display). Derive `beaufort_force` as a computed property from a lookup table — do not store it, it is redundant.
>
> **Encode the safety inversion explicitly.** The Surf Score should *reward* offshore wind for quality, but the **safety gate must independently flag offshore wind ≥ 15 kt as a beginner hazard** and force a ratio reduction. Two separate code paths, two separate outputs: `quality_score` and `safety_verdict`. Never let a high quality score override a safety block — that is the failure mode that drowns people.

---

## 8. Tides

### 8.1 Tide states to model

**Height state:** `LOW` / `MID` / `HIGH` — position between the day's low and high water marks.
**Direction state:** `INCOMING` (flooding, low→high, also called "pushing") / `OUTGOING` (ebbing, high→low, "dropping") / `SLACK` (at the turn).

These are **independent dimensions** — "mid tide incoming" and "mid tide outgoing" are meaningfully different conditions and must both be representable.

### 8.2 Why tides matter

- **Wave shape:** tide changes the water depth over the sandbar or reef, which changes where and how the wave breaks. Too little water and the wave breaks harshly on a shallow bottom; too much and it loses energy and fails to break at all.
- **Best for beginners: low to mid tide.** Waves break slower and closer to shore, producing longer, more manageable rides — ample time to get to the feet.
- **Mid tide is the general sweet spot:** enough water to prevent a harsh closeout on a dry bottom, but not so much that the wave loses all its energy.
- **Incoming vs outgoing — a safety distinction, not just quality:**
  - **Incoming (flood)** is generally **better and safer for beginners**. Wave energy and tidal flow combine in the same shoreward direction — the well-known **"tidal push"** gives waves extra consistency, power and shape.
  - **Outgoing (ebb)** makes waves **weaker** and, critically, **generates more current** because wave direction and tidal flow interfere. Rip current risk is elevated on a dropping tide.
- **Operational reality:** lesson times must be scheduled around the tide, not the clock. This is why Bloowatch markets **"tide-aware scheduling to plan the right windows automatically"** as a headline surf-school feature.

Surfing England lists tide as one of the three forces a coach must assess:
> "Tide (height & flooding / ebbing direction)"

Note the phrasing — the governing body itself models tide as **height + direction**, exactly the two-field design.

> ### RECOMMENDATION
> Model tide as **two fields, not one**: `tide_height_state` (`LOW`/`MID_LOW`/`MID`/`MID_HIGH`/`HIGH`) and `tide_direction` (`INCOMING`/`OUTGOING`/`SLACK`), plus the raw `tide_height_m` and `next_high_water`/`next_low_water` datetimes for scheduling.
>
> Add a per-beach `TideWindow` model: `beach`, `skill_level`, `optimal_tide_state_from`, `optimal_tide_state_to`, `optimal_direction`. **Every surf break has its own tide preference** — hardcoding "mid tide is best" globally is wrong and will make the product feel dumb to anyone who knows their local break. Ship low→mid + incoming as the beginner default and let each school tune it per beach. This per-beach tuning is the highest-value "smart" feature in the whole system.
>
> Feed `TideWindow` into a **scheduling suggester**: given a date and a beach, propose the 2-hour lesson slots whose tide window best matches the target skill level. That is the feature surf school owners will actually pay for.
>
> Apply a **safety modifier for outgoing tide**: elevated rip risk. Reduce the Surf Score's safety component and, on a strongly ebbing spring tide, trigger the ratio-reduction path ("lateral rips" is one of Surfing England's named reduction triggers).

---

## 9. Swell — Height and Period

### 9.1 Swell period is the quality driver

Swell period (seconds between successive wave crests passing a point) determines how much **energy** a swell carries.

| Period | Classification | Surf quality |
|---|---|---|
| **< 8 s** | Local wind swell | **Weak**, disorganised, choppy, closely spaced |
| **8 – 10 s** | Wind swell / short-period | **Fair** — average conditions |
| **10 – 12 s** | Emerging groundswell | **Good** — "the power of ground swells is taking effect", worth pursuing |
| **12 – 16 s** | Groundswell | **Excellent** — powerful, clean, well-formed waves |
| **> 16 s** | Long-period groundswell | Very powerful; "feels the bottom" far out; can be far bigger than the raw height suggests |

**Groundswell** travels long distances, has periods of **11–20+ s**, and produces organised, powerful waves. **Windswell** forms close to shore, has periods **under 10 s**, and is choppy.

**The key insight for the algorithm:** *"A 3 ft swell at 7 seconds is weak, soft, and crumbly. A 3 ft swell at 15 seconds carries far more energy — it 'feels the bottom' earlier, stands up taller, and breaks with real power."* **Height is less important than period when forecasting quality.**

**Safety corollary:** for beginners this inverts. A long-period swell of nominally "small" height hits **much harder** than the number suggests. A 1.2 m / 16 s swell is not a beginner lesson even though 1.2 m is under the 1.5 m ceiling.

> ### RECOMMENDATION
> Store `swell_height_m`, `swell_period_s`, `swell_direction_deg`, and (if the data source provides it) secondary swell components. **Do not gate lessons on height alone.** Compute an **effective energy proxy**:
>
> ```python
> # Wave energy flux is proportional to H^2 * T
> energy_index = (swell_height_m ** 2) * swell_period_s
> ```
>
> Then apply the beginner gate on `energy_index` as well as raw height. Suggested beginner ceiling: `energy_index <= 20` (e.g. 1.5 m @ 8 s ≈ 18; 1.2 m @ 16 s ≈ 23 → correctly blocked). Calibrate against the school's own logged sessions after the first season — this is a genuinely defensible "AI" component: learn the threshold from cancelled-vs-completed session outcomes.

---

## 10. Mapping Conditions to Skill-Level Suitability

### 10.1 The two authoritative sources

**Surfing England (regulatory, mandatory for approved English schools):**
> "All beginner & novice improver surfing lessons will be conducted on safe, sandy, beach locations where waves are **less than 1.5 metres** and in surf conditions that are clearly safe and tolerable for the group and individuals."

> "If the conditions are bigger than 1.5 metres then the lateral rips of the location will be a tolerable risk to the group, the individual coach must assess the conditions on each occasion."

> "All beginner & improver surfing at both lower & higher ability will be conducted on **beach break** wave conditions."

Surfing England names the **three forces** that must be assessed:
> "Swell (strength, direction, size) · Tide (height & flooding/ebbing direction) · Wind/air/sea (force, direction and temperature)"

Surfing England sets **no minimum wave size** — it is "a surf coach's professional judgment decision". But it defines minimum conditions for a lesson to count:
> "There will be enough force in the sea conditions to propel a surfer and surfboard forward" and "enough time, for a reasonable effort to be made by the surfer, to stand up and ride the wave with control."

**Academy of Surfing Instructors (Recreational Surfing Standards):**

| Level | Wave size | Conditions | Wind | Equipment |
|---|---|---|---|---|
| **Beginner** | **No larger than 1½ ft** — "gentle breaking waves of white water less than 1½ feet" | Sandy beach, even gradient, **chest-deep water or less** | **< 20 km/h** | Soft surfboard, soft fins, leash |
| **Intermediate** | **No larger than 3 ft** | Beach break with **peeling (spilling) waves**, free of submerged hazards | **< 20 km/h** | Fibreglass board or soft board until confident |
| **Advanced** | **3 ft to 6 ft** | Beach, reef, point or river-mouth breaks; **open face peeling waves**; rips present and manageable | **< 35 km/h** | — |

Advanced additionally requires: strong swimming ability over distance, high fitness for repeated paddling, ability to navigate crowded line-ups and **handle potential hold-downs**.

### 10.2 General industry wave-height guidance (corroborating)

| Level | Wave height |
|---|---|
| Very first lesson | **Whitewater under 1 ft** (ankle–knee), riding straight |
| Beginner (green waves) | **1–3 ft** (knee to waist-high), up to ~1 m |
| Intermediate | **2–4 ft** ideal for skill progression; **3–6 ft** (1–2 m) usable range |
| Advanced | **6–12 ft** (2–3.7 m) |

Body-reference conversions used by surfers: knee-high ≈ 1.5 ft, waist-high ≈ 2–3 ft, chest-high ≈ 3–4 ft, head-high ≈ 5–6 ft.

### 10.3 Consolidated suitability matrix (implement this)

Reconciling the sources: Academy of Surfing Instructors is **stricter** (1.5 ft whitewater for beginners) because it describes *the waves the student rides*; Surfing England's 1.5 m is the *absolute ceiling for the session to run at all*. Both are real — use them as **two different thresholds**.

| Level | Ideal wave height | Max wave height (hard block) | Max wind (any direction) | Max offshore wind | Preferred tide | Min period | Max period |
|---|---|---|---|---|---|---|---|
| `FIRST_TIMER` | 0.3–0.5 m (1–1.5 ft) whitewater | **0.8 m** | **11 kt** (20 km/h) | **12 kt** | Low–Mid, incoming | – | **12 s** |
| `BEGINNER` | 0.5–0.9 m (1.5–3 ft) | **1.2 m** | **11 kt** (20 km/h) | **15 kt** | Low–Mid, incoming | – | **14 s** |
| `IMPROVER_LOW` | 0.6–1.0 m | **1.5 m** (SE regulatory ceiling) | **13 kt** | **18 kt** | Mid | – | **14 s** |
| `IMPROVER_HIGH` | 0.8–1.2 m | **1.5 m** (SE regulatory ceiling) | **15 kt** | **20 kt** | Mid | 7 s | **16 s** |
| `INTERMEDIATE` | 0.9–1.8 m (3–6 ft) | **2.0 m** | **15 kt** (20 km/h base) | **20 kt** | Any (per break) | 8 s | – |
| `ADVANCED` | 1.8–3.0 m (6–10 ft) | **3.7 m** | **19 kt** (35 km/h) | **25 kt** | Any (per break) | 8 s | – |

> ### RECOMMENDATION
> Implement a `SafetyGate` service that is **completely separate** from the Surf Score. It returns a verdict enum, never a number:
>
> ```python
> class SafetyVerdict(models.TextChoices):
>     GO = "GO"                        # all clear
>     GO_WITH_REDUCED_RATIO = "GO_RR"  # run, but cut group size
>     COACH_JUDGEMENT = "JUDGE"        # borderline — requires explicit coach sign-off with a note
>     NO_GO = "NO_GO"                  # blocked
> ```
>
> Hard `NO_GO` triggers (non-overridable by any user role):
> 1. **Red flag flying** and no lifeguard-designated area assigned to the school.
> 2. Wave height above the level's **max wave height**.
> 3. Assigned instructor has **any non-`CURRENT` mandatory certification**.
> 4. Group size exceeds the level's **max ratio**.
> 5. No **rescue board** assigned to the group.
> 6. `FIRST_TIMER`/`BEGINNER` group assigned a **non-soft-top** board.
>
> `GO_WITH_REDUCED_RATIO` triggers (Surfing England's named factors): small children, crowded break, bigger surf, lateral rips, strong winds — reduce by 2 per two factors, 4 for three or more.
>
> `COACH_JUDGEMENT` should require a typed note that is stored on the session. Surfing England is explicit that:
> > "Any lesson cancellation decisions caused by changing sea conditions will be made by the **coaches/instructors in charge of the lesson on the day, not by centre managers or surf school owners**."
>
> **Encode this authority model in the permission system.** The coach assigned to the session must be able to cancel it; a manager or owner must **not** be able to override that cancellation. This is unusual for business software — most systems give the manager more power — and getting it right is both a legal-compliance feature and a strong trust signal to the coaching staff.

---

## 11. Beach Safety Flags, Procedures, and Incident Reporting

### 11.1 The flag standard

The authoritative source is **ILS Lifesaving Position Statement LPS-14, "Beach Safety and Information Flags"** (approved 2004, revised 27/07/2010), which underpins **ISO 20712-2:2007 — Water safety signs and beach safety flags, Part 2: Specifications for beach safety flags — Colour, shape, meaning and performance**. ISO 20712 has three parts: Part 1 (water safety signs, 2008), Part 2 (beach safety flags, 2007), Part 3 (guidance for use, 2020).

**Full flag table (ILS LPS-14, Table 1):**

| Flag | Meaning (verbatim) | Pantone | Shape |
|---|---|---|---|
| **Yellow** | **Medium hazard.** Moderate surf and/or currents are present. Weak swimmers are discouraged from entering the water. For others, enhanced care and caution should be exercised. | PMS 124 | Rectangle |
| **Red** | **High hazard.** Rough conditions such as strong surf and/or currents are present. All swimmers are discouraged from entering the water. Those entering the water should take great care. | PMS 186 | Rectangle |
| **Red over Red (double red)** | **Water is closed to public use.** | PMS 186 | Two rectangles |
| **Purple** | **Marine pests present** — jellyfish, stingrays, sea snakes or other marine life which can cause minor injuries. **Not** intended to indicate sharks. | PMS 266 | Rectangle |
| **Red over Yellow** | **Recommended swimming area with lifeguard supervision.** Used in **pairs** to mark a designated supervised zone where swimming/bodysurfing is permitted; used **singly** to indicate swimming is permitted in front of the flag under supervision. | PMS 186 / PMS 124 | Rectangle, equal parallel halves |
| **Black and White (Quartered)** | **Watercraft area.** Used in **pairs** to mark a zone for surfboards and other non-powered watercraft. | PMS 6 / white | Four equal quarters: black upper-left & lower-right |
| **Yellow flag with central black ball** | **Watercraft use prohibited** (e.g. no surfboards). | PMS 124 / PMS 6 | Yellow rectangle, central black ball **500 mm diameter** |
| **Orange windsock** | **Offshore winds present; inflatables should not be used.** | PMS 166 | Cone, 500 mm at hoist tapering to 300 mm, **1500 mm long** |
| **Red and White (Quartered)** | **Emergency evacuation.** Swimmers should leave the water — dangerous marine creature (shark, crocodile), pollution/spillage, or lifeguards need to search the water (e.g. a lost child). | PMS 186 / white | Four equal quarters: red upper-left & lower-right |

**Standard flag size: 750 mm × 1000 mm**, polyester or other suitable material.

**RNLI (UK) uses the same core system**, expressed for the public as: red-and-yellow = lifeguarded area, safest place to swim/bodyboard, always swim between the flags; **black-and-white chequered** = surfboards and other non-powered craft (paddleboards, kayaks, kitesurfers, windsurfers) — never swim or bodyboard there; **red** = danger, do not enter the water; **orange windsock** = offshore or strong wind, never use inflatables.

### 11.2 Operating rules (ILS LPS-14 §8)

- Flags are **only** to be used on beaches where **lifesavers qualified to ILS standards are on duty**. Flags cannot assist those in distress and are **not a substitute** for trained, equipped rescuers.
- **Except for the double red**, yellow and red flags **shall not be flown at the same time**. They indicate general conditions for the **entire beach**, not a particular area.
- The lowest point of a flag must be **not less than 2 metres above ground level**.
- Flags must be **changed as conditions change**; zoning flags moved as conditions dictate; informational flags (watercraft prohibition, offshore wind) **removed when not required**.
- Flag systems should only operate during a **prescribed and well-publicised period each day**.
- Flags must be **replaced once torn or faded**.

### 11.3 Surf school safety procedures (Surfing England, mandatory)

**Required documentation:**
- **Normal Operating Procedures (NOPs)** — daily routines, aims and objectives.
- **Emergency Action Plan (EAP)** — all instructors must be familiar with it and know how to operate it should an incident occur.
- **Safeguarding Policy.**
- **Written Health & Safety Risk Assessments**, generic and per-location, covering three zones: **off-beach / on-beach / in-water**, including reception and wetsuit/board changing areas. Submitted yearly; reviews recorded in the **daily beach log book**; pre/mid/post-season reviews sent to head office.
- **"Flat-water" and "big-seas" policy & practices statement.**
- **Lesson plans** evidencing each level taught.
- **Public liability insurance up to £5 m.**
- **Daily Duty Log** recording learning outcomes and **the number of students in each lesson**.

**Lifeguard liaison:**
- "A lifeguarded beach is **highly recommended**."
- "All lifeguards' advice **must be heeded**, particularly when a beach or beach area is red flagged: **There are no exceptions to this rule.**"
- Where an authorised Beach Lifeguard (BLG) service operates, **the coach must liaise with the BLG on a session basis**.
- "**No surf lesson will take place whilst a beach is red flag** unless the BLG has provided a dedicated area for the surf school's activities."

**Equipment safety requirements:**
- Rescue board **per group**, kept on the beach within **200 m** of the water NOP, **minimum 8 ft length, minimum 22 in width**.
- Fully-equipped first aid kit within **200 m**.
- **Warning whistles** or equivalent clear audio/visual signalling.
- **Hi-vis identity vests**: all students in bright colours; **instructor in a different colour** from the students; instructor vest recommended to be printed **"INSTRUCTOR"** or **"SURF COACH"**.
- Leashes in good order **without cuts, abrasions or knots**.
- **Efficient emergency telephone contact** available at all times, within reasonable access of the entire group if lone working.

### 11.4 Incident reporting

Surfing England's requirement:
> "Any serious accident or incident at your school that requires First Aid (or other actions) must be followed by a formal (written) school 'accident / incident report' and **signed in triplicate** by the person in charge of action and lesson and the student involved in the incident. For very serious accidents or incidents, a copy of all completed accident/incident reports to be sent to Surfing England Head Office as soon as possible after completion; a copy should be retained by the school and **a copy given to the student (usually within 72 hours of incident)**."

**Incident report fields** (composite of Surfing England requirements, standard incident-report practice, and Surf Life Saving's SurfGuard-style record types):

| Group | Fields |
|---|---|
| **Identification** | Report reference, incident type (`FIRST_AID` / `RESCUE` / `NEAR_MISS` / `PREVENTATIVE_ACTION` / `MARINE_STING` / `EQUIPMENT_FAILURE` / `LOST_PERSON` / `PROPERTY_DAMAGE`), severity |
| **When/where** | Date, time, beach, precise location (`OFF_BEACH` / `ON_BEACH` / `IN_WATER`), water depth |
| **Who** | Casualty (name, age, DOB, contact, medical notes from booking), instructor in charge, other staff, witnesses |
| **Context** | Session FK, session group FK, group size at time, level being taught, equipment in use (board ID, wetsuit ID, leash) |
| **Conditions** | **Frozen `ConditionsSnapshot`**: wave height, swell period/direction, wind speed/direction/class, tide state & direction, water temp, air temp, flag flying, lifeguard service on duty |
| **What happened** | Narrative description, mechanism of injury, body part affected |
| **Response** | First aid given (by whom, qualification), equipment used, emergency services called (which, time called, time arrived), lifeguard involvement, casualty outcome/disposal |
| **Follow-up** | Reported to NGB (bool + date), reported to HSE/RIDDOR (bool + date), copy given to student (bool + **date, must be ≤72 h**), risk assessment reviewed (bool), corrective actions |
| **Sign-off** | Signature of person in charge of action, signature of instructor in charge of lesson, signature of student/guardian, timestamps |

> ### RECOMMENDATION
> Model `BeachFlag` as a `TextChoices` enum using the ILS LPS-14 set exactly (`YELLOW`, `RED`, `DOUBLE_RED`, `PURPLE`, `RED_YELLOW`, `BLACK_WHITE_QUARTERED`, `YELLOW_BLACK_BALL`, `ORANGE_WINDSOCK`, `RED_WHITE_QUARTERED`). Because ILS says red and yellow **may not fly simultaneously** (except double red), model the hazard flag as a **single-valued field** and the zoning/information flags (`RED_YELLOW`, `BLACK_WHITE_QUARTERED`, `ORANGE_WINDSOCK`, `YELLOW_BLACK_BALL`) as **independent booleans** — they are a different category and can coexist. Getting this structurally right prevents impossible states.
>
> Implement the red-flag rule as an absolute gate: `flag in (RED, DOUBLE_RED, RED_WHITE_QUARTERED)` ⟹ `NO_GO`, overridable **only** by setting `lifeguard_designated_area=True` with the BLG contact name recorded. `RED_WHITE_QUARTERED` (emergency evacuation) must additionally trigger an **immediate recall workflow** — push notification to every instructor with an in-progress session.
>
> Build `Incident` with the field groups above and a **`conditions_snapshot` FK that is never null and never updated**. Enforce the 72-hour student-copy rule with a scheduled check that escalates overdue reports. Add `signature_*` fields with timestamps to satisfy "signed in triplicate" digitally.
>
> Make `RESCUE`, `NEAR_MISS` and `PREVENTATIVE_ACTION` first-class incident types, not just injuries. Near-miss and preventative-action data is what actually improves safety, and surf lifesaving organisations capture it as standard.
>
> Model `EmergencyActionPlan` and `RiskAssessment` as versioned documents attached to a `Beach`, with a `reviewed_on` date and an annual review reminder — because Surfing England audits these on the annual on-site visit and asks to see the daily NOP log book, incident report forms, insurance certificate, risk assessment, and every coach's certification and DBS status. **Build the audit pack as a one-click export.** That is a concrete, saleable feature.

---

## 12. Surf Camp Package Structure

The dominant format is **7 nights / 8 days**, though 5–14 day variants exist.

**Typical inclusions:**
- **Accommodation** (7 nights), often tiered: shared dorm / twin / private room.
- **All meals** — breakfast, lunch, dinner — plus snacks and drinks.
- **5 full days of surf lessons** (leaving arrival/departure days and often one rest day).
- **Surf coaching and all equipment** (board + wetsuit).
- **~6 yoga sessions** — complementary training for flexibility, balance, core strength, mindfulness.
- **Video analysis and photos** — progress tracking through the week.
- **Theory sessions** — water safety, tide theory, surf etiquette, forecasting.
- **Airport transfers** and **transport to and from surf spots** (the spot changes daily with conditions — this is why transport is bundled).
- **Optional extras**: massage, guided local tours, waterfall/nature excursions, rest-day activities.

**Skill levels:** most camps cater to **complete beginner through intermediate**.

**Package/pricing patterns observed in lesson products generally:** multi-lesson bundles priced as **"4 for the price of 3"**, and tiered private/semi-private/group variants of the same package.

> ### RECOMMENDATION
> Model a camp as a **`Package`** = an ordered collection of **`PackageItem`** rows, each pointing at a `Product` (lesson, yoga session, video analysis, meal plan, accommodation, transfer) with a `day_offset` and `quantity`. Do **not** model a camp as a single opaque product — the operational reality is that each constituent session still needs an instructor, a ratio check, equipment assignment, a conditions snapshot, and possibly a per-day beach change.
>
> Add `Package.accommodation_tier` and price the package as `base_price + tier_supplement`, and support a **`PackageDiscount`** rule ("4 for 3") as a percentage or free-unit rule so the bundle logic is data-driven.
>
> Crucially: **the surf spot changes daily with conditions.** Model `CampDay` with a **nullable `beach` that is assigned the evening before** based on the forecast — and make that assignment a direct consumer of the AI Surf Score. "Which beach should tomorrow's camp go to?" is the flagship demo of the whole system.

---

## 13. Synthesis — Specification for the AI Surf Score

### 13.1 Architecture: two outputs, never one

```
ConditionsSnapshot ──┬──> SafetyGate   ──> SafetyVerdict (GO / GO_RR / JUDGE / NO_GO)   [hard rules]
                     └──> SurfScorer   ──> quality_score 0–100 + per-level suitability   [soft scoring]
```

**The safety verdict always wins.** A `NO_GO` displays as `NO_GO` even if the quality score is 98. Never blend them into one number.

### 13.2 Proposed quality score (0–100), per skill level

```python
def surf_score(c: ConditionsSnapshot, level: SkillLevel, beach: Beach) -> int:
    # 1. Wave height fit (weight 30) — distance from the level's ideal band
    s_wave = gaussian_fit(c.wave_height_m, level.ideal_wave_min, level.ideal_wave_max)

    # 2. Swell period (weight 20)
    #    <8s -> 0.3 | 8-10 -> 0.55 | 10-12 -> 0.8 | 12-16 -> 1.0 | >16 -> 0.85
    #    For FIRST_TIMER / BEGINNER, INVERT above 12s (too powerful)
    s_period = period_curve(c.swell_period_s, level)

    # 3. Wind (weight 25) — direction class x speed
    #    OFFSHORE 5-15kt -> 1.0 | OFFSHORE 15-20 -> 0.7 | CROSS_OFFSHORE -> 0.85
    #    CROSS_SHORE <10kt -> 0.6 | ONSHORE <10kt -> 0.4 | anything >20kt -> 0.15
    #    For beginners, cap OFFSHORE contribution — offshore is a hazard for them
    s_wind = wind_curve(c.wind_class, c.wind_speed_kt, level)

    # 4. Tide (weight 15) — match against this beach's TideWindow for this level
    s_tide = beach.tide_window_fit(level, c.tide_height_state, c.tide_direction)

    # 5. Water temperature / comfort (weight 10)
    s_temp = temp_comfort(c.water_temp_c, level)   # beginners stand still, penalise cold harder

    raw = 30*s_wave + 20*s_period + 25*s_wind + 15*s_tide + 10*s_temp
    return round(raw)
```

**Score bands for the UI:** 0–24 Poor · 25–44 Marginal · 45–64 Fair · 65–84 Good · 85–100 Excellent.

### 13.3 What makes it genuinely "smart" rather than a formula

1. **Per-beach tide windows** learned from logged session outcomes.
2. **Energy index** (`H² × T`) beginner threshold calibrated from the school's own completed-vs-cancelled sessions.
3. **Camp beach assignment** — rank all the school's beaches for tomorrow's forecast and recommend one per camp group level.
4. **Feedback loop** — after each session, capture a one-tap coach rating (conditions as expected? too big? too small?) and regress the score against it.

> ### RECOMMENDATION
> Build `SafetyGate` **first and separately**, with exhaustive unit tests, before building any scoring. It is the component with legal consequences. Give it a pure-function interface (`ConditionsSnapshot + SessionGroup + Instructor → SafetyVerdict`) with no database writes, so it is trivially testable and can be called from booking validation, session start, and the forecast view alike.
>
> Store every computed score with its **input snapshot and the algorithm version** (`score_algorithm_version` char field). When you retune the weights, historical scores must remain interpretable — otherwise your feedback loop is training on shifting ground.
>
> Ship the weights in a `ScoringProfile` model (one row per school, editable) so tuning does not require a deploy.

---

## 14. Sources

**Primary / regulatory**
- [Surfing England — Surf School Scheme Regulations and Application (PDF)](https://www.surfingengland.org/wp-content/uploads/2019/10/Surf-Eng-Surf-School-Scheme-Oct19.pdf) — the single most important source: ratios, wave-height ceilings, equipment, documentation, incident reporting
- [Surfing England — Surf Coaching: A return to 1:8 Ratios for lessons](https://surfingengland.org/surf-coaching-1-to-8-ratios)
- [International Life Saving Federation — LPS-14 Beach Safety and Information Flags (PDF)](https://www.ilsf.org/wp-content/uploads/2019/01/LPS-14-2010-Flags.pdf)
- [ISO 20712-2:2007 — Beach safety flags: colour, shape, meaning and performance](https://www.iso.org/standard/39683.html)
- [ISO 20712-1:2008 — Water safety signs](https://www.iso.org/standard/39682.html) · [ISO 20712-3:2020 — Guidance for use](https://www.iso.org/standard/79604.html)
- [RNLI — Beach safety: flags and signs](https://rnli.org/water-safety/beach-safety/flags-and-signs)
- [RNLI — How RNLI beach lifeguards keep beaches safe](https://rnli.org/what-we-do/rnli-beach-lifeguards/about-rnli-beach-lifeguards/how-do-we-keep-beaches-safe)
- [NOAA / Weather Prediction Center — Beaufort Wind Scale](https://www.wpc.ncep.noaa.gov/html/beaufort.shtml)

**Certification**
- [ISA — Renewing Your Certificate](https://isasurf.org/learning/renew-your-certificate-in-source/) · [ISA Courses](https://isasurf.org/learning/isa-courses/) · [ISA Coaching](https://isasurf.org/learning/isa-coaching/)
- [George's Surf School — ISA Level 1 & Level 2 Surf Coach Qualifications](https://www.georgessurfschool.com/isa-level-1-surf-coach-qualification)
- [Surf Coach Academy — ISA Level 2 Course Overview](https://www.surfcoachacademy.com/courses/4-isa-level-2-coaching-course-overview)
- [RLSS UK — Qualifications and Awards](https://www.rlss.org.uk/our-qualifications-and-awards)
- [Era Adventures — International Beach Lifeguard Awards](https://www.era-adventures.co.uk/training-qualifications/international-beach-lifeguard-awards)
- [Astutis — First Aid at Work Requalification Guide](https://www.astutis.com/astutis-hub/blog/first-aid-at-work-requalification-guide)
- [GOV.UK — DBS checks in sport, working with children](https://www.gov.uk/government/publications/dbs-guidance-leaflets/dbs-checks-in-sport-working-with-children)

**Skill standards and conditions**
- [Academy of Surfing Instructors — Recreational Surfing Standards](https://www.academyofsurfing.com/recreational-surfing-standards)
- [Academy of Surfing Instructors — How do tides affect surf conditions?](https://www.academyofsurfing.com/news/how-do-tides-affect-surf-conditions)
- [Surfline — Groundswell vs. Windswell](https://www.surfline.com/surf-news/groundswell-vs-windswell/2439) · [Surfline — Tides and surfing](https://www.surfline.com/surf-news/tides-and-surfing/1107)
- [Surfertoday — The importance of swell period in surfing](https://www.surfertoday.com/surfing/the-importance-of-swell-period-in-surfing)
- [SurfSpotGuide — Swell Period Chart](https://www.surfspotguide.com/surf-guide/swell-period-chart) · [Wave Size for Beginners](https://www.surfspotguide.com/surf-guide/wave-size-for-beginners)
- [WindUp — Best Wind for Surfing: Offshore, Onshore & Glass](https://www.windup.live/blog/wind-speed-for-surfing/)
- [Red Bull — High tide vs low tide surfing](https://www.redbull.com/us-en/high-tide-low-tide-surfing)

**Equipment**
- [Global Surf Industries — Surfboard Finder & Volume Calculator](https://us.surfindustries.com/pages/surfboard-finder-and-volume-calculator)
- [Surfertoday — The surfboard volume calculator](https://www.surfertoday.com/surfing/the-surfboard-volume-calculator)
- [Rip Curl — Wetsuit Temperature & Thickness Guide](https://www.ripcurl.com/blogs/products/wetsuit-thickness-guide) · [Wetsuit Size Chart & Fit Guide](https://www.ripcurl.com/blogs/products/wetsuit-size-chart-fit-guide)
- [O'Neill EU — What thickness wetsuit do I need?](https://eu.oneill.com/blogs/all/wetsuit-thickness)
- [C-Skins — Wetsuit Water Temperature Guide](https://c-skins.com/pages/what-thickness-wetsuit-do-i-need)
- [Cleanline Surf — The Ultimate Surfboard Repair Guide](https://www.cleanlinesurf.com/blogs/surf/the-ultimate-surfboard-repair-guide)
- [Boardcave — Surfboard Ding Repair](https://www.boardcave.com/information/surfboard-ding-repair)
- [San Diego Surf School — How to Spot Damage on Your Surfboard](https://www.sandiegosurfingschool.com/surfboard-damage-and-repair/)

**Software landscape**
- [Bloowatch — Surf School Booking & Management Software](https://www.bloowatch.com/en/surf-schools)
- [WaveRez — Online Reservation Software for Surf Schools](https://www.waverez.com/solutions/surf-school/)
- [Yoplanning — Surf School Software](https://www.yoplanning.com/en/industries/surf-school-software)
- [Lueira — Management software for surf schools](https://lueira.com/en/surf-school-software/)
- [CaptainBook — 9 Best Tour Booking Software Platforms Compared (2026)](https://www.captainbook.io/blog/best-tour-booking-software-in-2026-9-platforms-compared)
- [automate.travel — Tour Booking Software Pricing 2026](https://automate.travel/booking-engine-pricing/)

---

## 15. Open Questions for the Team

1. **Jurisdiction.** The ratio and documentation rules above are the **Surfing England** scheme. If the target school operates in Portugal, Spain, Morocco, Türkiye or Australia, the *numbers* may differ but the *shape* of the model does not. Ship Surfing England as the default `ComplianceProfile` and make the thresholds data.
2. **Conditions data source.** The whole Surf Score depends on a forecast API (swell height/period/direction, wind speed/direction, tide, water temp). This needs its own research pass covering licence terms, rate limits and cost — Stormglass, Open-Meteo Marine, WorldTides, Surfline API and Windy all have materially different commercial terms.
3. **Celery on Windows.** Certification-expiry alerts, nightly forecast pulls, and 72-hour incident-report escalation all need scheduled work. Celery's Windows support is limited; on Windows 11 native, a `manage.py` command driven by **Windows Task Scheduler** is the lower-risk path for v1. Worth its own decision note.
