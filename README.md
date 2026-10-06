SENTINEL MPLADS

AI-Assisted Investigation Prioritization System for MPLADS Works

SENTINEL MPLADS is an AI-assisted system designed to help investigators prioritize MPLADS works that may require further review.

Instead of treating every project equally, SENTINEL analyzes each work against its relevant peer context and combines multiple analytical signals to identify unusual patterns.

> **SENTINEL does not declare a work fraudulent. It identifies unusual patterns and helps investigators decide where to focus their attention.**

---

## 🚨 Problem

MPLADS contains a large number of development works, making comprehensive manual review difficult.

Important patterns such as unusual costs, concentration of works, repeated descriptions, temporal irregularities, and stage-related anomalies can remain difficult to identify when reviewing large datasets manually.

---

## 💡 Our Solution

SENTINEL creates an evidence-backed investigation priority for each work.

### Core Pipeline

**MPLADS Work**  
↓  
**Context Engine**  
↓  
**Anomaly Detection**  
↓  
**Pattern & Evidence Fusion**  
↓  
**Risk + Confidence**  
↓  
**Investigation Priority**

---

## 🔍 Key Features

- **Context-Aware Analysis**  
  Compares a work with relevant peers based on state, constituency, category, and year.

- **Cost Anomaly Detection**  
  Identifies unusual cost patterns compared with comparable works.

- **Description Similarity**  
  Detects potentially repeated or unusually similar work descriptions.

- **MP / Constituency Pattern Analysis**  
  Identifies unusual concentration and distribution patterns.

- **Temporal Analysis**  
  Examines unusual timing and work-stage patterns.

- **Stage Consistency Analysis**  
  Identifies patterns that may warrant additional review.

- **Explainable Risk Scoring**  
  Shows the signals contributing to a work's risk score instead of producing a black-box result.

- **Investigation Priority Queue**  
  Helps investigators focus their limited review capacity on the works that deserve attention first.

---

## 📊 Dashboard

The system provides an overview of the analyzed MPLADS works, including:

- Risk distribution
- High and critical investigations
- Category-wise analysis
- Investigation priority
- Confidence indicators
- Work-level analytical findings

---

## 🧠 Investigation View

For individual works, SENTINEL provides:

- Risk assessment
- Contributing indicators
- Recommended investigation actions
- Signal fingerprint
- Peer context
- Project details
- Evidence and analytical findings

This allows reviewers to understand **why** a work was prioritized.

---

## 🛠️ Technology Stack

### Frontend
- React
- Vite
- React Router
- Leaflet / React-Leaflet
- Recharts
- Lucide React

### Backend
- Python
- FastAPI
- Uvicorn
- Pydantic

### Data & Analysis
- Pandas
- Statistical anomaly detection
- NLP-based similarity
- Multi-signal risk scoring

### Deployment
- Vercel
- Docker-based services

---

## 🏗️ System Architecture

```text
                MPLADS DATA
                     │
                     ▼
              CONTEXT ENGINE
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
     COST       DESCRIPTION     TEMPORAL
   ANALYSIS     SIMILARITY       ANALYSIS
       │             │             │
       └─────────────┼─────────────┘
                     ▼
          MP / CONSTITUENCY / STAGE
                  SIGNALS
                     │
                     ▼
             EVIDENCE FUSION
                     │
              ┌──────┴──────┐
              ▼             ▼
          RISK SCORE     CONFIDENCE
              │             │
              └──────┬──────┘
                     ▼
          INVESTIGATION PRIORITY
