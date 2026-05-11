# Real-world pattern: **kwargs access with None value
def build_response(**kwargs):
    profile = kwargs.get('profile')
    title = profile.title.strip()  # profile may be None
    return {'title': title}

result = build_response(user_id=1, profile=None)
print(result)
