# QueueFlow AI — Project Context, Requirements & System Design Understanding

## 1. Your Role and Current Objective

Act as a Senior Software Engineer, System Architect, and Technical Mentor working with me on a real-world software engineering project.

The project name is **QueueFlow AI — AI-Powered Virtual Queue Management System**.

We are following a professional Software Development Life Cycle (SDLC), similar to how a software team would build a real product.

**IMPORTANT: At this stage, your only task is to understand and analyze the project. Do not implement anything yet.**

Do not generate application code, create files, install packages, modify the repository, or start development. First, understand the complete context below and explain your understanding back to me.

---

## 2. Project Overview

QueueFlow AI is a responsive web application designed to help businesses manage physical queues and provide customers with a virtual queue option.

The application should work on:
- Mobile phones
- Tablets
- Laptops and desktop computers

Our initial use case is a small business with customers waiting in a physical queue, such as a hospital department, service counter, or similar establishment.

The problem we want to solve is simple:

Customers waste time standing in queues without knowing how long they will have to wait. Business owners may struggle to estimate crowd levels and manage waiting customers efficiently.

QueueFlow AI combines computer vision, queue management, waiting-time estimation, and virtual token management to make this process more convenient.

This is an MVP (Minimum Viable Product). We want to build a working, understandable, maintainable application first, then improve it gradually.

## 3. The Two Main Users

The MVP has only two main user interfaces.

### A. Owner Dashboard

The owner is responsible for managing the queue.

The owner should be able to:
1. Sign in securely.
2. Create and manage a queue session.
3. Upload a recorded video showing the current queue.
4. Analyze the video to estimate the number of people using computer vision.
5. Review and manually correct the estimated count.
6. See the estimated waiting time.
7. Receive a recommendation about whether a virtual queue may be useful.
8. Set the cutoff for the physical queue and activate the virtual queue.
9. Display or share a public link/QR code so new customers can join the virtual queue.
10. See virtual tokens and their statuses.
11. Mark customers as called, served, or cancelled.
12. Update operating details such as the number of active service counters and average service time.
13. See updated queue counts and waiting-time estimates.

The owner remains in control of important decisions. The system recommends actions; it does not automatically make operational decisions on the owner's behalf.

### B. Customer Dashboard / Token Tracking Page

Customers should not need to install a mobile application.

A customer should be able to open a link or scan a QR code using a browser.

The customer should be able to:
1. Open an active virtual queue.
2. Enter a phone number or email address as the contact method.
3. Request a token.
4. Receive a unique token number and a secure tracking link.
5. View the token status.
6. View the estimated number of people ahead.
7. View the estimated waiting time.
8. View a recommended time to return.
9. Receive an approaching-turn alert supported by the MVP.

The customer-facing page must be simple, mobile-friendly, and easy to understand.

A separate customer account system is not required for the initial MVP unless a genuine requirement emerges.

---

## 4. Complete End-to-End Workflow

Understand the intended workflow before suggesting implementation details.

### Step 1: Owner starts a queue session

The owner signs in and creates a queue session for the business or service counter.

### Step 2: Owner uploads a recorded video

The owner uploads a recorded video containing the physical queue.

For the first version, use recorded-video upload and analysis. Do not assume that continuous live-camera streaming is required.

### Step 3: Computer vision analyzes the video

OpenCV handles video processing, and a suitable YOLO person-detection model identifies people in video frames.

The system estimates the number of people in the relevant queue area.

The implementation must consider:
- A configurable queue region or Region of Interest (ROI).
- Avoiding repeated counting of the same person across multiple frames.
- Suitable frame sampling or object tracking.
- Detection errors, occlusion, and crowded scenes.
- Video upload validation and processing errors.
- A timestamp for the analysis result.
- Owner review and manual count correction.

Do not simply add person detections from every frame, because the same person may appear in multiple frames.

The video analysis provides an estimate. It does not guarantee a perfectly accurate count.

### Step 4: Estimate waiting time

The application uses information such as:
- Estimated queue size.
- Number of active service counters.
- Average service time per customer.
- Customers already waiting in the physical queue.
- Earlier virtual tokens that have not yet been served.

Use a transparent baseline formula:

Estimated waiting time in minutes ≈ people ahead × average service time per person ÷ active service counters.

Example: If 12 people are ahead, average service time is 4 minutes, and 2 counters are active, the estimated wait is approximately 24 minutes.

Handle invalid or missing inputs, including zero active counters. The system must never divide by zero or present an invalid estimate as reliable.

The formula gives an approximation, not a guarantee. Clearly communicate this to users.

### Step 5: Recommend virtual queue activation

When the queue reaches a configurable threshold, the system may recommend that the owner activate the virtual queue.

The owner reviews the situation and decides whether to activate it.

The threshold must not be treated as a universal fixed value without justification.

### Step 6: Owner defines the physical queue cutoff

The owner decides how many customers will continue in the physical queue and when new customers should join the virtual queue.

The system must not assume that camera detection alone determines which individual customer owns which token.

The owner remains responsible for confirming the physical queue situation.

### Step 7: Customers join the virtual queue

Customers arriving after the cutoff scan the QR code or open the public link and request a virtual token.

The backend generates a unique token number within the queue session and a sufficiently random, unguessable tracking identifier.

The system should account for the remaining physical queue and earlier virtual tokens when estimating how many people are ahead.

Only a small amount of necessary customer information should be collected.

### Step 8: Track the token

The customer uses the tracking page to check:
- Token number.
- Current status.
- People ahead.
- Estimated waiting time.
- Recommended return time.

As the owner marks tokens as called or served, the application recalculates estimates for affected waiting customers.

### Step 9: Notify customers

The MVP should support an approaching-turn alert through the open application page or another simple in-app/browser mechanism that can be reliably implemented.

Do not promise guaranteed background push notifications in the initial version.

Paid SMS, WhatsApp messaging, and paid notification services are outside the initial scope.

Clearly distinguish an in-app alert from a persistent notification that works while a browser is closed.

---

## 5. Functional Requirements

Treat the following as our initial functional requirements.

- FR-01: Secure owner login.
- FR-02: Owner can create a queue session.
- FR-03: Owner can upload a recorded queue video.
- FR-04: Computer vision can detect people and estimate queue size.
- FR-05: Owner can review and correct the estimated count.
- FR-06: System estimates waiting time from queue and service information.
- FR-07: System recommends virtual queue activation when appropriate.
- FR-08: Owner controls the physical cutoff and activates/deactivates the virtual queue.
- FR-09: Customer can request a token through a public link or QR code.
- FR-10: System assigns a unique token number and secure tracking identifier.
- FR-11: Customer can view token status, people ahead, estimated waiting time, and recommended return time.
- FR-12: Owner can update token status.
- FR-13: Waiting-time estimates update when queue conditions or token statuses change.
- FR-14: The application provides the supported approaching-turn alert.
- FR-15: Owner and customer interfaces are responsive across mobile, tablet, and desktop screens.

## 6. Non-Functional Requirements

The application should prioritize:

**Usability:** Simple, understandable interfaces.

**Responsiveness:** Layouts should adapt to phones, tablets, laptops, and desktops.

**Reliability:** Graceful handling of invalid inputs, failed video processing, and database errors.

**Security:** Secure password storage, protected owner endpoints, validated uploads, and unguessable customer tracking links.

**Privacy:** No facial recognition or unnecessary identity collection. Customer contact information must not be exposed through public APIs.

**Maintainability:** Clear module boundaries, reusable services, configuration management, and readable code.

**Testability:** Unit tests for business logic and API tests for important workflows.

**Cost awareness:** Prefer free and locally runnable tools wherever practical. Avoid paid API dependencies in the MVP.

**Transparency:** Explain that computer-vision counts and waiting-time estimates are approximate.

---

## 7. Scope Freeze

### In scope for the MVP

- Responsive web application.
- Owner dashboard.
- Customer token-tracking page.
- Owner authentication.
- Queue-session management.
- Recorded-video upload and analysis.
- YOLO/OpenCV-based person detection and queue-size estimation.
- Manual correction of queue count.
- Basic waiting-time calculation.
- Owner-controlled physical queue cutoff.
- Virtual token generation.
- Token status management.
- Customer tracking using a secure link.
- In-app/browser alert while supported by the application.
- SQLite database using SQLAlchemy.
- Basic automated testing and documentation.

### Out of scope for the initial MVP

- Facial recognition.
- Automatically matching a detected person to a customer or token.
- Fully automated physical queue cutoff.
- Continuous live-camera streaming.
- Paid SMS or WhatsApp integrations.
- An LLM chatbot or an LLM-powered token-generation system.
- A complex multi-business SaaS platform.
- A separate admin dashboard.
- Unnecessary microservices or enterprise-level infrastructure.

Do not silently expand this scope. If you identify an important gap, explain it and label it as a proposed future enhancement.

---

## 8. Technology Stack

Our current proposed stack is:

### Frontend
- HTML
- CSS
- JavaScript
- Responsive UI for owner and customer pages

Keep the frontend simple for the MVP. Do not introduce a large frontend framework unless there is a clear technical reason and we agree to the change.

### Backend
- Python
- FastAPI
- Pydantic for request and response validation
- SQLAlchemy ORM
- SQLite for the initial database

### Computer Vision
- OpenCV for video reading and processing.
- A suitable YOLO model for person detection.
- Tracking or frame-sampling logic to reduce repeated counting.

Choose a model and its dependencies after checking what can run on the available hardware. Verify model availability, installation requirements, and license considerations before committing to a specific choice.

### Development and Testing
- Git and GitHub for version control.
- A Python virtual environment.
- Automated tests using a suitable Python testing framework.
- Environment variables for secrets and configuration.
- A `.env.example` file without real credentials.
- Clear setup instructions in `README.md`.

OpenCode is our coding agent. It is not itself the AI model. We intend to use an available free model/provider through OpenCode where practical. Free-tier availability, model access, and rate limits may vary.

The application itself does not need an LLM to generate tokens or calculate waiting times. The main AI component in the MVP is computer vision.

Do not assume that a free coding model eliminates all infrastructure constraints. Local model execution, model downloads, hardware usage, hosting, and external notification services have separate requirements.

---

## 9. Proposed System Architecture

Use a simple modular architecture, not unnecessary microservices.

### Component 1: Responsive Frontend
Responsibilities:
- Owner login and dashboard.
- Queue-session controls.
- Video upload.
- Queue analysis results.
- Token management.
- Public customer token-tracking page.
- Display updated queue information and alerts.

### Component 2: FastAPI Backend
Responsibilities:
- Validate requests.
- Authenticate the owner.
- Authorize access to owner-owned queue sessions.
- Expose the application's API.
- Coordinate services and database operations.

### Component 3: Computer Vision Service
Responsibilities:
- Read uploaded videos.
- Process selected frames.
- Detect people using YOLO.
- Apply queue-region and counting logic.
- Return a count estimate, analysis metadata, and errors when applicable.

Keep computer-vision processing separate from API route handlers.

### Component 4: Queue Management Service
Responsibilities:
- Manage queue sessions.
- Apply cutoff and activation settings.
- Generate tokens.
- Track token order and statuses.
- Recalculate people-ahead estimates.
- Coordinate physical queue counts with the virtual queue.

### Component 5: Waiting-Time Service
Responsibilities:
- Calculate approximate waits using active counters and average service time.
- Handle invalid or incomplete inputs.
- Recalculate affected customer estimates.
- Provide a recommended return time based on the current estimate.

### Component 6: Notification Service
Responsibilities:
- Determine when an approaching-turn alert is appropriate.
- Support the notification method selected for the MVP.
- Avoid claiming delivery when an alert cannot be confirmed.

### Component 7: Database
Responsibilities:
- Persist owners, queue sessions, queue snapshots, and tokens.
- Enforce appropriate relationships and uniqueness constraints.
- Keep customer-private information protected.

The intended data flow is:

Owner uploads video → FastAPI validates it → Computer Vision Service analyzes it → Owner reviews the estimated count → Queue Service applies the owner's queue settings → Waiting-Time Service calculates estimates → Database stores the required state → Owner and customer pages display the latest information.

---

## 10. Proposed Project Structure

This is a starting proposal, not an instruction to create these files now.

```text
queueflow-ai/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   └── security.py
│   ├── database/
│   │   ├── session.py
│   │   └── base.py
│   ├── models/
│   │   ├── owner.py
│   │   ├── queue_session.py
│   │   ├── token.py
│   │   └── queue_snapshot.py
│   ├── schemas/
│   │   ├── auth.py
│   │   ├── queue.py
│   │   └── token.py
│   ├── api/
│   │   └── routes/
│   │       ├── auth.py
│   │       ├── owner.py
│   │       └── public.py
│   ├── services/
│   │   ├── vision_service.py
│   │   ├── queue_service.py
│   │   ├── waiting_time_service.py
│   │   └── notification_service.py
│   └── repositories/
├── frontend/
│   ├── owner/
│   │   └── index.html
│   ├── user/
│   │   └── token.html
│   └── assets/
│       ├── css/
│       └── js/
├── data/
│   └── uploads/
├── tests/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Review this structure critically. Recommend additions or simplifications only with clear reasoning. For example, identify any missing initialization files or modules needed for the selected implementation.

Uploaded videos, environment secrets, virtual environments, local databases, and other generated data must not be committed to Git unnecessarily.

---

## 11. Initial Database Design

We currently expect four core entities.

### Owners
Possible fields:
- `id`
- `shop_name`
- `email` — unique
- `password_hash`
- `created_at`

Passwords must be hashed securely, never stored in plaintext.

### Queue Sessions
Possible fields:
- `id`
- `owner_id` — foreign key
- `people_count`
- `physical_people_remaining`
- `active_counters`
- `average_service_minutes`
- `status`
- `virtual_queue_enabled`
- `created_at`
- `updated_at`

Review whether `people_count` and `physical_people_remaining` represent distinct business concepts. Do not duplicate state without a reason.

### Tokens
Possible fields:
- `id`
- `session_id` — foreign key
- `token_number`
- `tracking_id` — unique and securely generated
- `customer_contact`
- `status`
- `created_at`
- `called_at`
- `served_at`

Token statuses should support at least `waiting`, `called`, `served`, and `cancelled`.

Decide carefully which estimates should be computed dynamically and which historical values are worth storing.

### Queue Snapshots
Possible fields:
- `id`
- `session_id` — foreign key
- `people_count`
- `estimated_wait_minutes`
- `captured_at`
- Relevant analysis metadata if genuinely needed

Use appropriate SQLAlchemy relationships, foreign keys, indexes, and uniqueness constraints.

Expected relationships:
- One owner can have many queue sessions.
- One queue session can have many tokens.
- One queue session can have many queue snapshots.

Identify any schema gaps or contradictions before implementation.

---

## 12. Proposed API Design

The following route list is a starting API contract to review.

### Authentication
- `POST /api/auth/login`

### Owner Queue Management
- `POST /api/queue-sessions`
- `POST /api/queue-sessions/{session_id}/analyze-video`
- `GET /api/queue-sessions/{session_id}/status`
- `PATCH /api/queue-sessions/{session_id}/settings`
- `POST /api/queue-sessions/{session_id}/activate-virtual-queue`

### Public Customer APIs
- `POST /api/public/queue-sessions/{session_id}/tokens`
- `GET /api/public/tokens/{tracking_id}`

### Owner Token Management
- `PATCH /api/owner/tokens/{token_id}/status`

Review whether additional endpoints are needed, such as registration or logout, depending on the chosen authentication strategy.

For every endpoint, identify:
- Purpose.
- Authentication and authorization requirements.
- Request schema.
- Response schema.
- Important validation rules.
- Expected error responses.

All owner operations must verify session ownership. Public tracking endpoints must not expose customer contact details, password data, or internal database identifiers unnecessarily.

Video uploads require sensible validation for file type, file size, errors, and storage location.

---

## 13. Important Computer-Vision Limitation

This limitation is fundamental to our design.

YOLO may identify and count people visible in a video, but it does not know which detected person corresponds to which customer token.

For example, the system cannot automatically conclude that a detected person is Token A05 simply because five people are visible.

The physical queue and virtual token queue must therefore be coordinated through owner-controlled rules and token status updates.

Any approach that claims automatic identification or perfect queue counting must be treated as incorrect for this MVP.

The application should show that vision results are estimates and allow the owner to review them before making queue decisions.

---

## 14. Security, Privacy, and Operational Constraints

Design with the following requirements in mind:
- Hash owner passwords securely.
- Protect owner-only API routes.
- Check that each owner can access only their own sessions and tokens.
- Use a sufficiently random tracking identifier for public token tracking.
- Do not use sequential token IDs as public access credentials.
- Avoid exposing customer contact information in public responses.
- Validate uploaded files and avoid unsafe file paths.
- Store secrets in environment variables.
- Exclude private uploads and local secrets from Git.
- Handle unavailable video-processing dependencies and model-loading failures gracefully.
- Never describe an estimate as a guaranteed waiting time.
- Do not add face recognition.
- Avoid claiming that a browser alert will work when the browser is completely closed unless an appropriate notification mechanism is implemented and tested.

---

## 15. Responsive Application Expectations

The application is a single web product with different experiences for the owner and customer.

The owner dashboard is intended primarily for laptop/desktop use but must also adapt to tablets and mobile screens.

The customer token page is intended primarily for mobile use but must also work on a laptop or tablet.

The customer should be able to open their tracking link directly in a browser. There is no requirement to build separate native Android or iOS applications.

Local development and remote access are different:
- During development, the application can run locally.
- Multiple devices must access the same deployed/shared backend to see common queue data from different networks.
- Hosting and deployment decisions can be made in a later SDLC phase.

---

## 16. Professional SDLC Plan

We intend to build the application in controlled phases.

### Phase 1: Requirements Analysis and Scope Freeze
Problem definition, users, functional requirements, non-functional requirements, MVP scope, and limitations.

### Phase 2: System Design
Architecture, module responsibilities, project structure, database relationships, API contracts, wireframes, waiting-time calculation, and computer-vision constraints.

### Phase 3: Project Setup and Backend Foundation
Git setup, virtual environment, dependencies, FastAPI application, configuration, SQLite/SQLAlchemy setup, health-check endpoint, and basic tests.

### Phase 4: Database Models and Authentication
Implement models, relationships, validation, secure password handling, and protected owner endpoints.

### Phase 5: Queue Session Management
Create and manage queue sessions, configure counters and average service time, and test session ownership.

### Phase 6: Computer-Vision Pipeline
Implement recorded-video upload, validation, OpenCV processing, YOLO person detection, queue-region support, counting/tracking logic, result review, and manual correction.

### Phase 7: Waiting-Time and Virtual Queue Logic
Implement estimates, physical cutoff rules, virtual queue activation, secure token creation, queue ordering, status transitions, and recalculation.

### Phase 8: Frontend Development
Build the responsive owner dashboard and customer token-tracking page and connect them to the APIs.

### Phase 9: Integration, Notifications, and Testing
Connect all modules and test real workflows, errors, authorization, mobile layouts, queue updates, and supported alerts.

### Phase 10: Documentation and Deployment
Prepare setup instructions, environment configuration, architecture documentation, user workflow documentation, and deployment guidance.

Treat this as a proposed phased plan. Recommend reordering only when there is a strong engineering reason.

We must complete and review one phase before moving to the next. Avoid making all the changes in a single large code-generation step.

---

## 17. Engineering Workflow Rules

Follow these rules throughout the project:

1. Understand requirements before coding.
2. Explain architectural decisions and trade-offs in plain language.
3. Work in small, reviewable steps.
4. Keep modules focused on specific responsibilities.
5. Avoid unnecessary abstractions and premature complexity.
6. Add tests for important logic and edge cases.
7. Diagnose errors instead of hiding them with quick fixes.
8. Never invent successful test results or claim that code works without verification.
9. Keep documentation and project state updated.
10. Do not install packages or introduce paid APIs without explaining why.
11. Do not rewrite approved architecture silently.
12. Clearly distinguish confirmed requirements, current assumptions, and proposed improvements.
13. When implementation begins, inspect the existing repository first and preserve useful existing work.
14. Explain commands before asking me to run commands that modify the environment or project.
15. Use the available free coding model responsibly and break work into small tasks if context or rate limits are restrictive.

---

## 18. What You Must Do Now

Again, **do not implement anything yet**.

First, analyze the project context above and respond with the following sections:

1. **Your understanding of QueueFlow AI:** Explain the real-world problem and our solution in your own words.
2. **End-to-end workflow:** Explain what the owner does, what computer vision does, how a customer obtains a token, and how the queue progresses.
3. **User roles and MVP features:** Distinguish owner functionality from customer functionality.
4. **Requirements and scope:** Summarize what is included and excluded.
5. **Architecture understanding:** Explain each component and how data moves between them.
6. **Database understanding:** Explain the four proposed entities and their relationships.
7. **API understanding:** Explain how the proposed endpoints work together.
8. **Computer-vision limitations:** Explain how we will avoid repeated counting and why the system cannot identify a person's token.
9. **Waiting-time logic:** Explain the calculation and its limitations.
10. **Risks and design gaps:** Identify contradictions, missing requirements, technical risks, or decisions we need to settle before coding. Clearly label assumptions and recommendations rather than silently changing the scope.
11. **SDLC roadmap:** Explain the order in which we will build and test the application.
12. **Your confirmation:** State what you understand, what remains uncertain, and whether the proposed architecture is feasible for our MVP using the intended technologies and budget constraints.

Use clear, practical language. Do not merely repeat this document word-for-word. Demonstrate understanding by explaining how its parts work together.

If something needs a decision, identify it clearly and recommend a reasonable MVP default. Do not stop unnecessarily over minor details.

**Stop after this analysis and wait for my approval before creating or modifying any files or writing implementation code.**