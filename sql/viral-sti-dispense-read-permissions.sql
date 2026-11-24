-- Additional table-scoped read permissions for Viral Load, STI and Medication Dispense registers.
GRANT SELECT ON report.viral_load_investigation_register TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.patient_client_profile TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.laboratory_request_order TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON terminology.type_of_specimen TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON terminology.laboratory_test_reason TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.investigation_group_order TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON report.sti_register TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON report.dispense_report TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.dispense TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON terminology.program_diagnosis TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.person_diagnosis TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON terminology.diagnosis TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.patient_sti_contact TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON terminology.formulation TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON report.medicine_batch_register TO 'attendance_api'@'127.0.0.1';
GRANT SELECT ON consultation.medicine_batch_issue TO 'attendance_api'@'127.0.0.1';
