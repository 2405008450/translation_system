from uuid import UUID
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Segment, SegmentRevision, User, FileRecord
from app.services.review_sync import lock_project, active_member, record_edit, enqueue, can_write
project_id=UUID('5509e9f1-3234-4ce4-8a62-9ae5dfb57d5e')
with SessionLocal() as db:
 lock_project(db,project_id)
 segment=db.get(Segment,UUID('23ffcf08-9a17-4bef-ae63-91464ac483be'))
 revision=db.get(SegmentRevision,UUID('b8cbdac6-7af5-4a1c-8e26-188b20100728'))
 assert db.get(FileRecord,segment.file_record_id).project_id==project_id
 assert segment.version==2 and segment.target_text==revision.after_text
 assert revision.segment_id==segment.id and revision.status=='pending' and revision.source=='manual'
 assert revision.before_text=='' and not segment.project_sync_disabled
 assert active_member(db,segment.id) is None
 user=db.get(User,revision.author_id)
 assert can_write(db,segment,user)
 record_edit(db,segment,revision.before_text,user,True,'manual')
 member=active_member(db,segment.id)
 assert member is not None
 task=enqueue(db,segment,member.group_id,segment.version,user)
 print('Recovered original revision group',member.group_id,'queued task',task.id)
 db.commit()
