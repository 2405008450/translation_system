-- 投影表同步触发器改为仅在检索相关字段变化时重建投影行。
-- 之前任何 UPDATE（如仅修改译文）都会先 DELETE 再 INSERT 投影记录，
-- 造成 GIN 索引无谓的写放大。CREATE OR REPLACE 幂等，可重复执行。

CREATE OR REPLACE FUNCTION sync_memory_entry_search_projection()
RETURNS TRIGGER AS $$
DECLARE
    normalized_length INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM memory_entry_search WHERE entry_id = OLD.id;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        -- 仅译文/元数据变化时投影无需任何改动。
        IF NEW.source_normalized IS NOT DISTINCT FROM OLD.source_normalized
           AND NEW.source_language IS NOT DISTINCT FROM OLD.source_language
           AND NEW.target_language IS NOT DISTINCT FROM OLD.target_language
           AND NEW.collection_id IS NOT DISTINCT FROM OLD.collection_id
           AND NEW.source_hash IS NOT DISTINCT FROM OLD.source_hash THEN
            RETURN NEW;
        END IF;
        DELETE FROM memory_entry_search WHERE entry_id = NEW.id;
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- 常规路径下 INSERT 不存在旧投影行；保留清理以兼容手工修数。
        DELETE FROM memory_entry_search WHERE entry_id = NEW.id;
    END IF;

    IF NEW.source_normalized IS NULL
       OR NEW.source_language IS NULL
       OR NEW.target_language IS NULL THEN
        RETURN NEW;
    END IF;

    normalized_length := char_length(NEW.source_normalized);
    IF normalized_length <= 0 THEN
        RETURN NEW;
    END IF;

    INSERT INTO memory_entry_search (
        entry_id,
        collection_id,
        source_language,
        target_language,
        source_hash,
        source_normalized,
        source_length,
        updated_at
    )
    VALUES (
        NEW.id,
        NEW.collection_id,
        NEW.source_language,
        NEW.target_language,
        NEW.source_hash,
        NEW.source_normalized,
        normalized_length,
        COALESCE(NEW.updated_at, NOW())
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
