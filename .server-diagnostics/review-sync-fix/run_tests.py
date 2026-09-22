import os, sys, unittest
from uuid import uuid4
from sqlalchemy import create_engine, text
from app.database import engine
name='review_sync_qa_'+uuid4().hex
url=engine.url.set(host='postgres',port=5432)
admin=create_engine(url,isolation_level='AUTOCOMMIT')
try:
 with admin.connect() as c: c.execute(text('CREATE DATABASE '+name))
 os.environ['REVIEW_SYNC_TEST_DATABASE_URL']=url.set(database=name).render_as_string(hide_password=False)
 sys.path.insert(0,'/tmp')
 if os.environ.get('PATCH_FILE'):
  import app.services.review_sync as module
  exec(compile(open(os.environ['PATCH_FILE']).read(),module.__file__,'exec'),module.__dict__)
 import test_review_sync_regression as tests
 suite=unittest.defaultTestLoader.loadTestsFromModule(tests)
 result=unittest.TextTestRunner(verbosity=2).run(suite)
finally:
 with admin.connect() as c: c.execute(text('DROP DATABASE IF EXISTS '+name+' WITH (FORCE)'))
sys.exit(not result.wasSuccessful())
