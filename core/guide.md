## 바닥판 안내: 메일 첨부
- 메일 도구(Gmail create_draft·send_message)와 Google Drive create_file은 파일을 base64 글자로 통째로 받습니다. PDF·HWPX·그림처럼 큰 파일은 길어서 잘리거나 깨지니 직접 붙이지 않습니다.
- 파일을 메일로 보낼 때: 받는 사람·제목·본문을 다 채운 초안(create_draft)만 만들고, 초안 링크와 붙일 파일(SendUserFile)을 같이 드립니다. 사용자가 Gmail에서 파일만 붙이고 보내기를 누릅니다. 아주 작은 글 파일만 직접 붙여도 됩니다.
- 받은 메일의 첨부를 읽을 때: 첨부를 내려받는 도구는 없습니다. 사용자에게 파일을 세션에 직접 올려 달라고 하거나, Gmail에서 「드라이브에 저장」한 뒤 Google Drive read_file_content로 읽습니다(PDF·워드·그림만 됨, HWPX는 직접 올려 달라고 함).
- 첨부를 못 붙였으면 못 붙였다고 말합니다. 붙인 척하지 않습니다.
