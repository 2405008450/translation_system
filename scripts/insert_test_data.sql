-- 向 translation_memory 表插入测试数据
-- source_text: 源语言（中文）
-- target_text: 目标语言（英文）

INSERT INTO translation_memory (source_text, target_text, source_hash, source_normalized, created_at, updated_at) VALUES
-- 技术文档类
('软件架构遵循微服务模式', 'The software architecture follows a microservices pattern', 'a1b2c3d4', 'software architecture follows microservices pattern', NOW(), NOW()),
('数据库连接池提高性能', 'Database connection pooling improves performance', 'e5f6g7h8', 'database connection pooling improves performance', NOW(), NOW()),
('API端点返回JSON格式的数据', 'The API endpoint returns JSON formatted data', 'i9j0k1l2', 'api endpoint returns json formatted data', NOW(), NOW()),
('身份验证由JWT令牌处理', 'Authentication is handled by JWT tokens', 'm3n4o5p6', 'authentication is handled by jwt tokens', NOW(), NOW()),
('缓存层减少数据库负载', 'The cache layer reduces database load', 'q7r8s9t0', 'cache layer reduces database load', NOW(), NOW()),

-- 商业文档类
('第四季度收入超出预期15%', 'Q4 revenue exceeded expectations by 15%', 'u1v2w3x4', 'q4 revenue exceeded expectations by 15 percent', NOW(), NOW()),
('季度报告将于下周一发布', 'The quarterly report will be released next Monday', 'y5z6a7b8', 'quarterly report will be released next monday', NOW(), NOW()),
('我们与GlobalTech的战略伙伴关系加强了我们市场地位', 'Our strategic partnership with GlobalTech strengthens our market position', 'c9d0e1f2', 'strategic partnership with globaltech strengthens market position', NOW(), NOW()),
('客户满意度提升至92%', 'Customer satisfaction rating improved to 92%', 'g3h4i5j6', 'customer satisfaction rating improved to 92 percent', NOW(), NOW()),
('合并将在财年底完成', 'The merger will be completed by end of fiscal year', 'k7l8m9n0', 'merger will be completed by end of fiscal year', NOW(), NOW()),

-- 日常对话类
('请把盐递给我好吗', 'Could you please pass me the salt', 'o1p2q3r4', 'could you please pass me the salt', NOW(), NOW()),
('今天天气真好，不是吗', 'The weather is lovely today, isnt it', 's5t6u7v8', 'weather is lovely today isnt it', NOW(), NOW()),
('我想预订晚餐', 'I would like to make a reservation for dinner', 'w9x0y1z2', 'i would like to make a reservation for dinner', NOW(), NOW()),
('最近的地铁站在哪里', 'Where is the nearest subway station', 'a3b4c5d6', 'where is nearest subway station', NOW(), NOW()),
('你能推荐一家附近的好餐厅吗', 'Could you recommend a good restaurant nearby', 'e7f8g9h0', 'could you recommend good restaurant nearby', NOW(), NOW()),

-- 医学健康类
('患者表现出季节性过敏症状', 'The patient shows symptoms of seasonal allergies', 'i1j2k3l4', 'patient shows symptoms of seasonal allergies', NOW(), NOW()),
('定期锻炼有助于维持心血管健康', 'Regular exercise helps maintain cardiovascular health', 'm5n6o7p8', 'regular exercise helps maintain cardiovascular health', NOW(), NOW()),
('医生开了抗生素治疗感染', 'The doctor prescribed antibiotics for the infection', 'q9r0s1t2', 'doctor prescribed antibiotics for infection', NOW(), NOW()),
('应该每天监测血压', 'Blood pressure should be monitored daily', 'u3v4w5x6', 'blood pressure should be monitored daily', NOW(), NOW()),
('均衡饮食提供必要的营养', 'A balanced diet provides essential nutrients', 'y7z8a9b0', 'balanced diet provides essential nutrients', NOW(), NOW()),

-- 教育类
('课程涵盖基本的编程概念', 'The course covers fundamental programming concepts', 'c1d2e3f4', 'course covers fundamental programming concepts', NOW(), NOW()),
('学生必须在周五前完成作业', 'Students must complete the assignment by Friday', 'g5h6i7j8', 'students must complete assignment by friday', NOW(), NOW()),
('讲座将在301教室举行', 'The lecture will be held in Room 301', 'k9l0m1n2', 'lecture will be held in room 301', NOW(), NOW()),
('在线注册下周开始', 'Online registration opens next week', 'o3p4q5r6', 'online registration opens next week', NOW(), NOW()),
('教科书在图书馆可以借到', 'The textbook is available in the library', 's7t8u9v0', 'textbook is available in library', NOW(), NOW()),

-- 法律类
('合同需要双方共同同意', 'The contract requires mutual agreement from both parties', 'w1x2y3z4', 'contract requires mutual agreement from both parties', NOW(), NOW()),
('法律顾问审核了条款和条件', 'Legal counsel reviewed the terms and conditions', 'a5b6c7d8', 'legal counsel reviewed terms and conditions', NOW(), NOW()),
('仲裁条款解决法庭外争议', 'The arbitration clause resolves disputes out of court', 'e9f0g1h2', 'arbitration clause resolves disputes out of court', NOW(), NOW()),
('必须保护知识产权', 'Intellectual property rights must be protected', 'i3j4k5l6', 'intellectual property rights must be protected', NOW(), NOW()),
('责任限制在第5条中有明确说明', 'The liability limitation is clearly stated in section 5', 'm7n8o9p0', 'liability limitation is clearly stated in section 5', NOW(), NOW()),

-- 旅游类
('酒店入住时间是下午2点', 'The hotel check-in time is at 2 PM', 'q1r2s3t4', 'hotel check in time is at 2 pm', NOW(), NOW()),
('我们提供历史街区的导览游', 'We offer guided tours of the historic district', 'u5v6w7x8', 'we offer guided tours of historic district', NOW(), NOW()),
('护照有效期必须至少为6个月', 'The passport must be valid for at least 6 months', 'y9z0a1b2', 'passport must be valid for at least 6 months', NOW(), NOW()),
('旅行保险覆盖海外医疗紧急情况', 'Travel insurance covers medical emergencies abroad', 'c3d4e5f6', 'travel insurance covers medical emergencies abroad', NOW(), NOW()),
('观光巴士每小时发车一次', 'The sightseeing bus departs every hour', 'g7h8i9j0', 'sightseeing bus departs every hour', NOW(), NOW()),

-- 科技类
('人工智能正在改变医疗保健行业', 'Artificial intelligence is transforming healthcare industry', 'k1l2m3n4', 'artificial intelligence is transforming healthcare industry', NOW(), NOW()),
('新智能手机具有改进的相机系统', 'The new smartphone features an improved camera system', 'o5p6q7r8', 'new smartphone features improved camera system', NOW(), NOW()),
('区块链技术确保数据完整性', 'Blockchain technology ensures data integrity', 's9t0u1v2', 'blockchain technology ensures data integrity', NOW(), NOW()),
('云计算降低基础设施成本', 'Cloud computing reduces infrastructure costs', 'w3x4y5z6', 'cloud computing reduces infrastructure costs', NOW(), NOW()),
('软件更新包括安全补丁', 'The software update includes security patches', 'a7b8c9d0', 'software update includes security patches', NOW(), NOW()),

-- 环境类
('气候变化影响全球天气模式', 'Climate change affects global weather patterns', 'e1f2g3h4', 'climate change affects global weather patterns', NOW(), NOW()),
('可再生能源减少碳排放', 'Renewable energy sources reduce carbon emissions', 'i5j6k7l8', 'renewable energy sources reduce carbon emissions', NOW(), NOW()),
('回收计划已转移了500吨废物', 'The recycling program has diverted 500 tons of waste', 'm9n0o1p2', 'recycling program has diverted 500 tons of waste', NOW(), NOW()),
('森林保护保护生物多样性', 'Forest conservation protects biodiversity', 'q3r4s5t6', 'forest conservation protects biodiversity', NOW(), NOW()),
('空气质量指数保持在良好范围内', 'Air quality index remains in the good range', 'u7v8w9x0', 'air quality index remains in good range', NOW(), NOW()),

-- 体育类
('足球队昨晚赢得了冠军', 'The football team won the championship last night', 'y1z2a3b4', 'football team won championship last night', NOW(), NOW()),
('定期训练提高运动表现', 'Regular practice improves athletic performance', 'c5d6e7f8', 'regular practice improves athletic performance', NOW(), NOW()),
('马拉松路线穿过风景优美的地区', 'The marathon route passes through scenic areas', 'g9h0i1j2', 'marathon route passes through scenic areas', NOW(), NOW()),
('运动员必须遵守严格的兴奋剂规定', 'Athletes must follow strict doping regulations', 'k3l4m5n6', 'athletes must follow strict doping regulations', NOW(), NOW()),
('体育场可容纳50000名观众', 'The stadium can accommodate 50000 spectators', 'o7p8q9r0', 'stadium can accommodate 50000 spectators', NOW(), NOW()),

-- 餐饮类
('厨师使用新鲜食材准备菜肴', 'The chef prepares dishes using fresh ingredients', 's1t2u3v4', 'chef prepares dishes using fresh ingredients', NOW(), NOW()),
('这种葡萄酒适合配红肉', 'This wine pairs well with red meat', 'w5x6y7z8', 'this wine pairs well with red meat', NOW(), NOW()),
('这家餐厅以其正宗的意大利菜而闻名', 'The restaurant is known for its authentic Italian cuisine', 'a9b0c1d2', 'restaurant is known for authentic italian cuisine', NOW(), NOW()),
('你能推荐一道好的素食选择吗', 'Can you recommend a good vegetarian option', 'e3f4g5h6', 'can you recommend good vegetarian option', NOW(), NOW()),
('甜点菜单包括自制糕点', 'The dessert menu features homemade pastries', 'i7j8k9l0', 'dessert menu features homemade pastries', NOW(), NOW()),

-- 金融类
('股市指数以历史新高收盘', 'The stock market index closed at record high', 'm1n2o3p4', 'stock market index closed at record high', NOW(), NOW()),
('利率影响借贷成本', 'Interest rates affect borrowing costs', 'q5r6s7t8', 'interest rates affect borrowing costs', NOW(), NOW()),
('多元化投资组合降低风险', 'Diversified investment portfolio reduces risk', 'u9v0w1x2', 'diversified investment portfolio reduces risk', NOW(), NOW()),
('审计确认了财务报表的准确性', 'The audit confirmed financial statement accuracy', 'y3z4a5b6', 'audit confirmed financial statement accuracy', NOW(), NOW()),
('加密货币波动性仍然是关注点', 'Cryptocurrency volatility remains a concern', 'c7d8e9f0', 'cryptocurrency volatility remains concern', NOW(), NOW()),

-- 娱乐类
('首映礼吸引了名人出席', 'The movie premiere attracted celebrity attendance', 'g1h2i3j4', 'movie premiere attracted celebrity attendance', NOW(), NOW()),
('流媒体服务争夺订阅者', 'Streaming services compete for subscribers', 'k5l6m7n8', 'streaming services compete for subscribers', NOW(), NOW()),
('音乐会门票在几分钟内售罄', 'The concert sold out within minutes', 'o9p0q1r2', 'concert sold out within minutes', NOW(), NOW()),
('数码摄影改变了这个行业', 'Digital photography revolutionized the industry', 's3t4u5v6', 'digital photography revolutionized industry', NOW(), NOW()),
('戏剧作品获得评论界好评', 'The theater production received critical acclaim', 'w7x8y9z0', 'theater production received critical acclaim', NOW(), NOW());

-- 验证插入结果
SELECT COUNT(*) as total_records FROM translation_memory;
