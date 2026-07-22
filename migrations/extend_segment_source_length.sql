-- 支持 english_variant_conversion 等更明确的句段来源标识。
ALTER TABLE segments
    ALTER COLUMN source TYPE VARCHAR(40);
