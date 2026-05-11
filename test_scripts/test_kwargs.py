def build_response(**kwargs):
    profile = kwargs.get('profile')
    title = profile.title.strip()
    return {'title': title}

result = build_response(user_id=1, profile=None)
print(result)
