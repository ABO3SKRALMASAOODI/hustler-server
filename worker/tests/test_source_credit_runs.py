from scene import text_layout


def test_adjacent_views_share_only_the_same_cited_event():
    views = [(479,499,("nyse",2),"New York Stock Exchange","NYSE · May 2019 IPO"), (499,518,("nyse",2),"New York Stock Exchange",None),
             (518,540,("nyse",3),"New York Stock Exchange",None), (550,580,("nyse",3),"New York Stock Exchange",None)]
    assert text_layout.shared_source_credits(views) == [(479,518,"NYSE · May 2019 IPO")]
