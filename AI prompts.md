

## Phase 1: Understand the project 

**1. Understand the project**
I want to build a Workforce Planning Platform. Explain the complete project in simple English. Tell me what problem it solves, who uses it, and how it works in a real company.

**2. Identify the users**
Identify all users of this application, such as employees, managers, HR partners, IT partners, and administrators. Explain what each user can do.

**3. Identify the main modules**
List the important modules required for this application. Explain the purpose of each module and how they connect.

**4. Explain the business workflow**
Explain the complete workflow from employee skill collection to workforce demand forecasting, skill-gap detection, and hiring or training recommendations.

**5. Define the requirements**
Create a clear list of functional and non-functional requirements. Keep the requirements realistic for a hackathon project.

**6. Identify the entities**
Identify all database entities required for this application. Explain each entity with a simple real-world example.

**7. Define user permissions**
Create a role-based access control plan. Explain which pages and actions each user role can access.

**8. Identify business rules**
List the business rules for employee skills, project requirements, allocations, leave, utilization, and skill gaps.

**9. Identify important calculations**
Explain how to calculate workforce capacity, employee utilization, demand, available capacity, and skill gaps. Include simple examples.

**10. Create the final project plan**
Prepare a step-by-step development plan covering database, backend, frontend, integrations, testing, and deployment. Do not write code yet.

## Phase 2: Architecture and database design 

**11. Design the application architecture**
Design a simple full-stack architecture for this project. Explain how the frontend, backend, database, and APIs communicate.

**12. Recommend the technology stack**
Recommend a suitable frontend, backend, database, and supporting libraries. Explain why each technology is suitable.

**13. Design the database**
Create a normalized relational database design for employees, roles, skills, employee skills, projects, project demand, allocations, and leave.

**14. Define table relationships**
Explain primary keys, foreign keys, one-to-many relationships, and many-to-many relationships for all tables.

**15. Design employee tables**
Create the database structure for employee details, role, department, availability, and employment status.

**16. Design skill tables**
Create tables for skills, employee skills, proficiency levels, and last-verified dates. Use a consistent proficiency scale.

**17. Design project tables**
Create tables for projects, required roles, required skills, demand quantities, and demand time periods.

**18. Design allocation tables**
Create tables to track employee assignments, allocation percentages, start dates, end dates, and leave.

**19. Design history tables**
Create a history or event-recording approach so that changes to skills, allocations, and project demand are traceable instead of silently overwriting history.

**20. Review the database design**
Review the complete schema for missing relationships, duplicate data, incorrect data types, and normalization problems. Provide the corrected schema.

## Phase 3: Backend development 

**21. Inspect the existing project**
Before writing code, inspect my current backend and frontend folder structures. Explain what already exists and what is missing. Do not create unnecessary files.

**22. Set up the backend**
Create the basic backend application using the selected technology. Keep the folder structure simple and explain every important file.

**23. Configure database connectivity**
Connect the backend to PostgreSQL using environment variables. Never hardcode database credentials. Add a safe example environment file.

**24. Create database models**
Implement the database models based on the approved schema. Make sure primary keys, foreign keys, constraints, and relationships are correct.

**25. Create API schemas**
Create request and response schemas for employees, skills, projects, demand, allocations, and workforce planning results.

**26. Build employee APIs**
Create APIs to add, view, update, search, and manage employees. Validate all incoming data.

**27. Build skills APIs**
Create APIs to add skills, assign skills to employees, update proficiency levels, and track verification dates.

**28. Build project and demand APIs**
Create APIs to manage projects and their workforce requirements by role, skill, quantity, and time period.

**29. Build allocation APIs**
Create APIs to assign employees to projects, update allocation percentages, record leave, and prevent invalid assignments.

**30. Build dashboard APIs**
Create APIs that calculate employee totals, project demand, available capacity, utilization, and skill gaps directly from database records.

## Phase 4: Workforce planning logic 

**31. Calculate workforce capacity**
Implement logic to calculate available employee capacity using working days, leave, and existing allocations. Explain the formula.

**32. Calculate utilization**
Calculate employee utilization as a percentage of available capacity. Identify underutilized and overutilized employees.

**33. Calculate project demand**
Calculate the required workforce for each project based on role, skill, demand quantity, and time period.

**34. Identify skill gaps**
Compare project-required skills with employee skills and proficiency levels. Identify missing skills and insufficient proficiency.

**35. Identify role gaps**
Compare project role demand with available employees in the same roles. Calculate the shortage or surplus.

**36. Recommend suitable employees**
Build a matching system that recommends employees based on role, skill proficiency, availability, and allocation capacity.

**37. Recommend training**
Identify employees who are close to meeting a required skill level and recommend relevant training to close their skill gaps.

**38. Recommend hiring**
Identify gaps that cannot reasonably be filled through current employee capacity or training. Recommend hiring needs by role and skill.

**39. Detect planning risks**
Identify over-allocation, underutilization, missing skills, insufficient capacity, and project demand risks.

**40. Validate planning calculations**
Create sample scenarios and verify that all workforce calculations are correct. Include edge cases such as leave, overlapping allocations, and missing skills.

## Phase 5: Frontend development 

**41. Design the frontend**
Create a clean, modern, professional user interface for the Workforce Planning Platform. Keep the layout simple and easy to navigate.

**42. Build the dashboard**
Create a dashboard with employee count, project count, total demand, available capacity, utilization, and skill-gap summaries. Load all values from backend APIs.

**43. Build employee management**
Create an employee page with a searchable table, filters, employee details, and add/edit forms.

**44. Build skill management**
Create a skill management page showing skills, proficiency levels, employee skill assignments, and verification dates.

**45. Build project management**
Create a project page showing project name, department, start date, end date, status, and required workforce.

**46. Build demand planning**
Create a demand planning page showing required roles, required skills, demand quantities, and time periods.

**47. Build allocation management**
Create an allocation page showing employee assignments, allocation percentages, availability, and project timelines.

**48. Build skill-gap visualization**
Create a page that clearly displays missing skills, insufficient proficiency, affected projects, and recommended actions.

**49. Build recommendations**
Create a page displaying employee matching, training recommendations, and hiring recommendations with explanations.

**50. Build reports**
Create a reporting page with workforce utilization, capacity gaps, project demand, skill gaps, and planning risks. Add useful filters and export options.

## Phase 6: Frontend and backend integration 

**51. Connect frontend to backend**
Connect every frontend page to the correct backend API. Do not use hardcoded or fake operational data.

**52. Implement API loading**
Implement loading states, error handling, empty states, and retry behavior for API requests.

**53. Implement forms**
Connect employee, skill, project, demand, and allocation forms to backend APIs. Validate the inputs before submission.

**54. Implement search and filters**
Make search, sorting, pagination, and filters work using real backend data.

**55. Implement dashboard refresh**
Ensure dashboard numbers update after relevant database changes. Avoid unnecessary repeated API calls.

**56. Implement role-based screens**
Show the correct navigation and actions according to the logged-in user's permissions.

**57. Implement notifications**
Display useful success, warning, and error messages when records are created, updated, or rejected.

**58. Handle empty database conditions**
Make sure the application works properly when there are no employees, projects, allocations, or skill records.

**59. Improve performance**
Inspect slow database queries, API response times, and unnecessary frontend requests. Optimize them without breaking existing functionality.

**60. Verify complete integration**
Test every frontend page against the backend and database. Report which pages work and which need fixing.

## Phase 7: Testing and validation 

**61. Create backend tests**
Write tests for employee, skill, project, demand, and allocation APIs. Use an isolated test database.

**62. Test database relationships**
Verify that foreign keys, constraints, and deletion rules work correctly.

**63. Test capacity calculations**
Test available capacity using different employee working schedules, leave periods, and allocations.

**64. Test utilization calculations**
Test underutilization, normal utilization, and over-allocation scenarios.

**65. Test skill-gap detection**
Create test cases for missing skills, insufficient proficiency, and employees who meet all required skills.

**66. Test recommendations**
Verify that recommended employees are actually available and match the required role and skills.

**67. Test data validation**
Test missing fields, invalid dates, negative allocations, duplicate records, and invalid proficiency levels.

**68. Test security**
Check authentication, authorization, sensitive environment variables, and unauthorized API access.

**69. Test the complete workflow**
Test the application from employee creation through skill assignment, project demand, allocation, and workforce recommendations.

**70. Perform final quality checks**
Run backend tests, frontend linting, type checks, and production builds. Fix all critical errors and report the results.

## Phase 8: Finalization and GitHub 

**71. Review the complete project**
Inspect the complete application and identify unused files, duplicate code, unnecessary folders, and incomplete features. Do not delete anything without explaining why.

**72. Clean the folder structure**
Make the project structure simple and professional. Preserve all working code and avoid creating unnecessary files.

**73. Review environment variables**
Check all environment files. Ensure real passwords, API keys, tokens, and database credentials are never committed.

**74. Prepare Git ignore rules**
Create or review `.gitignore` files to exclude virtual environments, caches, build folders, dependencies, and secrets while keeping required example configuration files.

**75. Prepare the database instructions**
Write simple instructions for creating the PostgreSQL database, applying the schema, loading initial data, and verifying the records.

**76. Prepare the README**
Create a beginner-friendly README explaining the project, features, technology stack, folder structure, database setup, backend setup, frontend setup, and testing commands.

**77. Prepare GitHub upload**
Inspect the project and list exactly which files should be committed. Exclude `.env`, `.venv`, `node_modules`, `.next`, cache files, and generated files that should not be uploaded.

**78. Verify the GitHub commit**
Guide me through `git status`, reviewing staged files, committing changes, connecting the remote repository, and pushing the main branch.

**79. Prepare the project demonstration**
Create a simple demonstration flow showing how an administrator adds employees, managers add project demand, the platform calculates capacity, and HR reviews skill gaps and recommendations.

**80. Perform the final project audit**
Act as a senior software engineer. Review the complete Workforce Planning Platform against the original requirements. Report completed features, missing features, bugs, security risks, test results, and final readiness. Do not claim a feature works unless it has been verified.

---

## Important instructions to give the AI with every prompt

- First inspect the existing files before making changes.
- Preserve existing working functionality and UI.
- Do not create unnecessary files or folders.
- Do not use hardcoded dashboard values or fake operational records.
- Keep business logic in the backend and database.
- Use environment variables for credentials.
- Write clean, beginner-friendly code.
- Explain which files you changed and why.
- Run the relevant tests after making changes.
- If something is unclear, ask before making a major architectural change.
- Never claim that something is complete without testing it.