"""The invented organizations that issue the sample documents.

Every organization, address, phone number, web address and person here is made up.
Phone numbers use the 555-01xx block that is reserved for fiction; web and e-mail
addresses use the reserved .example domain so nothing points to a real site.
"""

HOSPITAL_SYSTEM = {
    "name": "Seaside Healthcare System",
    "short": "Seaside",
    "hospital_caps": "SEASIDE REGIONAL HOSPITAL",
    "hospital": "Seaside Regional Hospital",
    "street_caps": "4820 MARINER BLVD",
    "city_line": "CORAL HAVEN, Florida 33019",
    "phone_paren": "(954)555-0142",
    "phone_dash": "954-555-0142",
    "pfs_street": "4820 Mariner Blvd, Suite 200",
    "pfs_city": "Coral Haven, FL 33019",
    "remit": ["Seaside Healthcare System", "PO Box 740318", "Atlanta, GA 30374-0318"],
    "email": "MyBill@seasidehs.example",
    "site": "www.seasidehs.example",
    "fap_url": "www.seasidehs.example/financialaid",
    "physicians_url": "https://www.seasidehs.example/physicians",
    "portal": "MyChart",
    "license_line": "A PUBLIC HOSPITAL LICENSED BY THE STATE OF FLORIDA",
}

# Separately billing specialty groups listed on the hospital papers (all invented).
SPECIALTY_GROUPS_B = [
    ("Shoreline ER Physicians", "Emergency Room", "888-555-0175"),
    ("Pathology Partners of S. Coral", "Laboratory", "866-555-0188"),
    ("Harbor Imaging Radiology", "Radiology/CT/MRI/Nuc Med/Imaging", "904-555-0123"),
    ("Brightwater Healthcare Group NEO", "Neonatology", "866-555-0164"),
    ("Brightwater Healthcare Group ANE", "Anesthesiology", "800-555-0137"),
    ("Pediatric Critical Care Partners", "Intensive Care", "954-555-0119"),
    ("Coastal EKG Associates", "Cardiology/Pulmonary", "954-555-0158"),
    ("Seaside Physician Group Practices", "Seaside Physician/Specialist", "954-555-0103"),
    ("Eastshore/Westshore Hospitalists", "Managed Care Hospitalist", "888-555-0196"),
]

SPECIALTY_GROUPS_A = [
    ("Shoreline ER Physicians", "Emergency Dept. Physician", "888-555-0175"),
    ("Pathology Partners of S. Coral", "Pathologist", "866-555-0188"),
    ("Harbor Imaging Radiology", "Radiologist", "904-555-0123"),
    ("Brightwater Healthcare Group ANE", "Anesthesiologist", "800-555-0137"),
]

INSURER = {
    "name": "Harborlight Health Next",
    "legal": "A Product of Harborlight Health Florida, Inc.",
    "legal_small": "A product of Harborlight Health Florida, Inc.",
    "street": "1450 Lighthouse Parkway",
    "city": "Tampa Bay, FL 33699",
    "group_name": "Harborlight Health Next",
}

ANESTHESIA = {
    "brand": "Meridian",
    "brand_sub": "PHYSICIAN SERVICES",
    "legal_caps": "BRIGHTWATER HEALTHCORP INC",
    "no_mail": ["DO NOT MAIL PAYMENTS HERE", "PO Box 5521", "Coral Springs, FL 33075-5521"],
    "remit": ["PO BOX 931774", "ATLANTA, GA 31193-1774"],
    "site": "pay.meridianhealth.example",
    "phone": "1-888-555-0129",
    "help_phone": "1-888-555-0131",
}

OBGYN = {
    "name": "Women First OB/GYN Care, LLC",
    "payable": "300_Women First OB/GYN Care, LLC",
    "member_of": "Seaside Regional Hospital (IP) 300",
}

PEDIATRIC = {
    "name": "LITTLE HARBOR PEDIATRICS",
    "sub": "FAMILY OF COMPANIES",
    "addr": ["1200 HARBOR POINT BLVD", "STE 400", "PORT CORAL FL 33018-2207"],
    "payable": "Little Harbor Pediatrics - PA",
    "pay_site": "https://payclinic24.example",
    "phone": "(954) 555-0177",
}

NEONATAL = {
    "name": "Cradleview-Obstetrix Medical Group",
    "caps": "CRADLEVIEW-OBSTETRIX MEDICAL GROUP",
    "brand": "cradleview",
    "brand_sub": "MEDICAL GROUP",
    "addr": ["PO BOX 740990", "ATLANTA GA 30374-0990"],
    "form_url": "www.cradleview.example/insuranceform",
    "phone": "(888) 555-0107",
    "footer_code": "CRADLVIEW1",
}
