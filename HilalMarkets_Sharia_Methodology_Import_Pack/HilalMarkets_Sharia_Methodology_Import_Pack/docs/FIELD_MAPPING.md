# Field Mapping

Map the package into existing domain models rather than creating duplicate systems.

| Package field | Intended domain concept |
|---|---|
| methodology_id | Methodology stable identifier |
| source_row_id | Idempotent external import key |
| external_status_source | Exact external status wording |
| normalized_status | Internal presentation/policy mapping |
| canonical_symbol_candidate | Candidate only; not identity proof |
| publication_state | Admin publication workflow |
| source_authority_section | External-source part of Passport |
| hilalmarkets_factual_profile | Separate AI-assisted factual part |
| source_detail_fields | Source-specific report fields |
| rights_state | Whether public commercial display is permitted |
| manual_verification_required | Mandatory admin gate |

Do not use `canonical_symbol_candidate` as the final foreign key. Resolve name, chain, contract/native state and official website first.
