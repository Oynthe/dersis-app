# DERSIS — Application Overview

## What is DERSIS?

DERSIS (Ders Programi Hazirlama Sistemi / Class Schedule Preparation System) is a desktop application for creating, optimizing, and managing weekly class timetables for educational institutions. Built with PyQt6, it provides an interactive visual interface for scheduling classes across days, time slots, and classrooms while respecting hard constraints and optimizing for soft preferences.

## Target Users

- **University scheduling offices** — departments responsible for semester timetable creation
- **School administrators** — managing weekly class schedules across multiple rooms and teachers
- **Educational coordinators** — anyone who needs to assign classes to time slots, rooms, and lecturers without conflicts

## The Problem It Solves

Manual timetable creation is a complex combinatorial problem. Schedulers must simultaneously satisfy:
- No lecturer can teach two classes at the same time
- No room can host two classes at the same time
- No student group can attend two classes at the same time
- Class durations must fit consecutive time slots
- Lecturer availability must be respected
- Room capacities must not be exceeded

Beyond these hard constraints, good schedules also minimize gaps in lecturer/student days, balance daily loads, avoid room switching, and respect preferences. DERSIS automates this with AI-assisted optimization while keeping the user in control.

## Core Workflow (Step by Step)

### 1. Initial Setup
User configures the scheduling environment:
- **Days**: Which weekdays are active (e.g., Monday-Friday)
- **Time Slots**: Available hours per day (e.g., 09:00, 10:00, ..., 17:00)
- **Classrooms**: Available rooms with capacities and types
- **Years/Branches**: Student groups (e.g., Year 1 Computer Science, Year 2 Mathematics)
- **Lecturers**: Teaching staff with optional availability constraints

### 2. Add Classes
Classes are added individually or via bulk Excel import. Each class specifies:
- Course name and code
- Assigned lecturer
- Target student groups (year + branch)
- Duration (number of consecutive slots)
- Allowed days/times (optional constraints)
- Room requirements (physical, online, lecturer office)
- Joint class grouping (classes that must be scheduled together)

### 3. Place Classes
Classes can be placed:
- **Manually** — drag-and-drop onto the timetable grid
- **Auto-place** — AI selects the best slot for a single class
- **Batch schedule** — AI places multiple unplaced classes at once
- **Full reschedule** — AI optimizes the entire timetable from scratch

### 4. Review & Adjust
The interactive timetable view shows:
- Color-coded classes by year group
- Conflict warnings and constraint violations
- Protection badges (locked, pinned, improve-only)
- Multiple view modes (per-room, per-lecturer, per-group, matrix)

Users can drag-drop classes to adjust, and the system validates each move in real time.

### 5. Optimize
The optimization engine offers:
- **Heuristic optimization** — greedy placement with lookahead scoring
- **Large Neighborhood Search (LNS)** — iterative destroy-and-repair improvement
- **CP-SAT solver** — exact constraint programming via Google OR-Tools
- **Preference learning** — the system adapts to user preferences over time

### 6. Analyze
The analytics dashboard provides:
- Schedule quality score (0-100, grades A-F)
- Per-lecturer metrics (gaps, compactness, teaching days)
- Per-group metrics (idle periods, day balance)
- Room utilization rates
- Actionable insights and improvement suggestions

### 7. Export
Finalized timetables export to:
- **Excel** (.xlsx) — multi-sheet workbook with formatting
- **CSV** — plain data export
- **PDF** — formatted printable schedules

### 8. Save & Load
Schedules are saved in encrypted `.egu` files with autosave support. The app maintains saves under `~/Documents/Dersis/saves/`.

## Key Differentiators

### Explainable AI
Every AI decision (auto-placement, optimization change, rejection) comes with a human-readable explanation. Users see *why* a class was placed in a particular slot, not just *where*.

### Preference Learning
The system logs user interactions (manual moves, accepted/rejected suggestions) and uses online gradient descent to adjust scoring weights. Over time, the optimizer learns what the user values most.

### Constraint Negotiation
When a class cannot be placed, the system doesn't just report failure. It analyzes *why* and suggests specific constraint relaxations or displacement strategies to resolve the conflict.

### Multi-Language Support
Full UI translation support for 22 languages, including all dialogs, tooltips, analytics reports, and export templates.

### Fully Offline
DERSIS runs entirely on the local machine. There is no login, account, license check, or network connection of any kind — the app opens directly into the main window and every feature (auto-scheduling, optimization, AI explanations, advanced analytics, and all export formats) is unlocked locally. Schedules persist in encrypted `.egu` files under `~/Documents/Dersis/`.

## Technical Stack

| Component | Technology |
|---|---|
| GUI Framework | PyQt6 (Fusion style) |
| Constraint Solver | Google OR-Tools CP-SAT |
| Data Import/Export | openpyxl, pandas, reportlab |
| Encryption | AES-256-GCM (cryptography library) |
| Build/Package | Nuitka, Embeddable Python, Inno Setup |
| Platform | Windows desktop (primary target) |
