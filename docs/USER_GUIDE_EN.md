# User Guide

A practical walkthrough of running a surf school day with this system.
Turkish version: [USER_GUIDE_TR.md](USER_GUIDE_TR.md)

---

## 1. Signing in

Go to `http://127.0.0.1:8000/` and sign in with your **username or e-mail**.

Tick *Keep me signed in* on your own machine only — on the shared reception PC
leave it clear, so the session ends when the browser closes.

Change your password under your avatar → *Change password*. Switch between
Turkish and English with the 🌐 icon in the top bar at any time.

**What you see depends on your role.** A rental clerk does not see revenue; an
instructor's dashboard leads with their own lessons. If a menu item is missing,
your role does not have it — ask a Manager rather than assuming it is broken.

---

## 2. The dashboard

Your starting point every morning.

| Tile | What it tells you |
|---|---|
| Today's Lessons | How many, and how full |
| Today's Students | Everyone in the water today |
| Today's Revenue | Taken so far today |
| Active Rentals | Gear currently out, with overdue flagged |
| Equipment Warnings | Items needing attention before you hand them out |
| Surf Conditions | Live wave, wind and the Surf Score per level |
| Instructor Availability | Who is working |
| Pending Payments | Money still owed |
| AI Alerts | Anything the assistant has flagged |

Below the tiles: today's schedule on the left, warnings and recent activity on
the right. Click any lesson to open it.

Press `/` anywhere to jump to the global search — it finds customers, students,
bookings, equipment and rentals. Typing an exact code (`EQ00042`, `BK000123`)
takes you straight to that record.

---

## 3. A first-time customer walks in

**Step 1 — create the customer.** Customers → *New customer*. Name, phone and
e-mail are enough to start. Add the emergency contact — you will want it and not
have time to ask later.

**Step 2 — create the student profile.** Students → *New student*, linked to that
customer. The important fields:

- **Surf level** — start honest. "First time" is not an insult, and it drives the
  safety rules.
- **Can swim** and swim distance — this is a safety field, not paperwork.
- **Weight** — the system uses it to recommend a board volume.
- **Medical conditions, medications, allergies** — asthma, epilepsy, a recent
  injury. The instructor needs to know before, not after.

**Step 3 — book the lesson.** See §4.

**Step 4 — take payment.** See §7.

For a returning customer, search their name and go straight to step 3.

---

## 4. Bookings

Bookings → *Calendar* shows the month. Each block shows the lesson type, time,
instructor and how full it is (`4/8`). Colours come from the lesson type.

### Making a booking

1. *New booking*.
2. Search for the customer — start typing; results appear as you type.
3. Choose the student.
4. Pick a lesson. Only lessons with free seats appear.
5. Set the number of participants.
6. **Watch the conflict panel.** It updates live and checks:
   - free seats
   - whether the student is already booked at that time
   - whether their level suits the lesson
   - instructor availability
   - the instructor-to-student ratio (stricter for under-18 groups)
   - any safety restriction on that student
7. Confirm.

If the panel shows a conflict, it says exactly what is wrong. **Do not work
around it** — the ratio and level rules exist because someone gets hurt when they
are ignored.

### If the lesson is full

Add the customer to the **waitlist**. When someone cancels, the first person on
the list is promoted automatically and you are notified.

### Cancelling

Open the booking → *Cancel*, give a reason. Cancelling more than 24 hours ahead
is free; inside 24 hours the system proposes a fee, which you can change. The
seat is released and the waitlist advances immediately.

Mark *No show* rather than cancelling when someone simply does not arrive — the
distinction matters for the statistics and for how you treat them next time.

---

## 5. Running a lesson

Open the lesson from the dashboard or calendar.

**Before the water:**
1. Check the **Surf Score** for the group's level. It is a computed number, and
   you can expand it to see exactly which factors produced it.
2. Complete the **safety briefing** checkbox — it records who signed it off.
3. Assign a **board** and a **wetsuit** to each student. The system suggests a
   board volume from weight and level, and a wetsuit thickness from the water
   temperature.
4. **Check in** each student as they arrive.

**After:**
1. *Complete lesson*. Everyone checked in is marked as attended, their lesson
   count and last-lesson date update.
2. Record a **skill assessment** for anyone who progressed. If you raise their
   level, their profile updates and future bookings use the new one.

> If the Surf Score says conditions are unsafe for the group's level, the
> decision to run or postpone is **yours**. The system gives you the numbers and
> the reasons; it does not overrule you, and it does not decide for you.

---

## 6. Equipment and rentals

### Inventory

Equipment → each item has an asset code and a QR label. Print labels from the
item page. Filter by category, status or condition; search by asset code, brand
or serial number.

Statuses: *Available*, *Rented*, *In lesson*, *Reserved*, *In maintenance*,
*Damaged*, *Lost*, *Retired*. Anything not available cannot be handed out.

### Renting gear out

Rentals → *New rental*:

1. Find the customer.
2. **Scan or type asset codes.** Each one adds a line and updates the running
   total.
3. Choose hourly, daily or weekly. A hire of a week or more automatically uses
   the weekly rate when it is cheaper.
4. Take the deposit.
5. Confirm — every item is marked *Rented*.

### Taking gear back

Rentals → find the rental → *Return*. For each item, set the condition it came
back in. If something is damaged:

- pick the damage type (ding, crack, fin, leash, wetsuit tear…),
- describe it,
- set a charge if appropriate.

The system computes the late fee if applicable, applies damage charges, settles
the deposit, and — when damage is reported — raises a maintenance record and
takes the item out of service automatically.

For a quick counter return, use *Quick return by asset code*.

### Maintenance

Maintenance shows open jobs. The **Predicted maintenance** board ranks equipment
by risk, computed from days since last service, rentals since then, total hours,
age and past failures. It is statistics from your own service history — not a
guess, and not an AI opinion. Each entry shows why it scored what it did.

---

## 7. Money

### Taking a payment

From a booking or rental, *Record payment*: amount, method, and the system
updates the balance and payment status. Everything is recorded against the
customer.

### Refunds

Open the payment → *Refund*. A refund creates a matching negative entry; the
original is never altered, so the history stays honest. Refunds need the
`finance.refund` permission.

### Packages

Sell a multi-lesson package at a discount: Finance → Packages. Lessons are drawn
down as they are used, and the balance is visible on the customer's page.

### Point of sale

POS → *Terminal*. Tap products or scan a barcode, adjust quantities, apply a
discount, take payment, print the receipt. Stock decrements automatically, and
the sale appears in the finance figures.

Voiding a sale returns the stock — it never deletes the record.

---

## 8. Reports and analytics

**Analytics** gives you the trends: revenue, bookings, occupancy, customer
retention, equipment utilisation, busiest hours. Every figure is shown against
the previous period of the same length. Change the period with the filter —
Today, 7 / 30 / 90 / 180 / 365 days, or a custom range.

Where a forecast is shown with thin data, the system says so rather than
presenting a confident-looking line. Treat a low-confidence forecast as a hint.

**Reports** produces documents: daily operations, revenue, payments, expenses,
profit and loss, bookings, cancellations, student lists, instructor performance
and commission, equipment inventory and utilisation, maintenance, rentals, camp
rosters, safety incidents.

Choose a report, set the filters, pick **PDF**, **Excel** or **CSV**.

> CSV exports are UTF-8 with a byte-order mark so Turkish characters open
> correctly in Excel.

---

## 9. Safety

- **Report an incident immediately** — near misses too. Safety → *New incident*.
  A near miss recorded today is how you avoid an injury next month.
- **Lifeguard roster** — who is covering which spot and when.
- **Emergency contacts** — printable; keep a copy by the phone.
- **Student restrictions** — a medical or skill restriction on a student is
  checked automatically when anyone tries to book them.
- **Warnings** — a warning suggested by the AI is clearly marked and is **not
  active** until a staff member acknowledges it. The acknowledgement records who.

---

## 10. The AI assistant

AI → *AI Assistant*. Ask in Turkish or English:

> "Summarise today's lessons."
> "Which lesson time suits beginners tomorrow?"
> "How much did revenue change over the last 30 days?"
> "Which surfboards are likely to need maintenance?"
> "Are tomorrow's conditions suitable for beginners?"

**It only reports what is in the database.** Every number comes from a real
query. If there is no data, it says so — it does not invent a plausible figure.
It also cannot show you anything your role cannot see.

Under each answer you can see which provider and model replied, how long it took,
and which lookups it ran.

Choose the mode in the composer:

| Mode | Use it when |
|---|---|
| **Local only** | Customer data must not leave the machine |
| **Automatic** | Normal use — cheap questions stay local, hard ones go to the cloud |
| **Cloud only** | You want the strongest model and have a key configured |

**Knowledge base** — AI → Knowledge lets you add your own manuals, policies and
safety procedures. The assistant will then quote them, with citations.

---

## 11. Backups

Backup & Restore → *Create backup now*. Do it before anything unusual: a big
price change, an import, an upgrade.

Every backup is checksummed. *Verify* re-checks it — a backup you have never
verified is a hope, not a plan.

**Restoring replaces current data.** The system verifies the backup, takes a
safety backup of the present state first, and asks you to type the backup code
before it proceeds. If the restore fails, it puts everything back.

Schedule daily backups (see [BACKUP_RESTORE.md](BACKUP_RESTORE.md)) and copy them
off the machine — they contain everything.

---

## 12. Learning the system

- **Training Center** — short interactive courses that walk you through real
  tasks: your first student, your first booking, taking a payment, running a
  report. Progress is saved.
- **Help Center** — reference for every screen, in both languages.
- **Onboarding wizard** — first-run setup for a new installation.

---

## 13. Quick reference

| I need to… | Go to |
|---|---|
| See today | Dashboard |
| Book a lesson | Bookings → New booking |
| Add a customer | Customers → New customer |
| Check someone in | Open the lesson → Check in |
| Rent gear out | Rentals → New rental |
| Take gear back | Rentals → Return |
| Report damage | Maintenance → Report issue |
| Take money | Booking/rental → Record payment |
| Sell a product | POS → Terminal |
| See the numbers | Analytics |
| Produce a document | Reports |
| Check the surf | Surf Conditions |
| Report an incident | Safety → New incident |
| Ask a question | AI → AI Assistant |
| Protect the data | Backup & Restore |
| See who changed what | Audit Log |

| Shortcut | Action |
|---|---|
| `/` | Global search |
| `Esc` | Close a dialog |
| `Enter` in the barcode box | Add the item |
