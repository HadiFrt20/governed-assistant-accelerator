-- Custom Unity AI Gateway policy: DENY any request whose message contains one of the configured keywords.
-- CEL transpile constraints: CONTAINS(LOWER(...)) only, no COALESCE / regex, every branch returns a reason.
CREATE OR REPLACE FUNCTION {fq}.policy_block_ip_leak(event VARIANT)
RETURNS VARIANT
LANGUAGE SQL
COMMENT '{comment}'
RETURN
  CASE
    WHEN ({conditions})
    THEN to_variant_object(named_struct('result', 'DENY', 'reason', '{reason}'))
    ELSE to_variant_object(named_struct('result', 'ALLOW', 'reason', ''))
  END
