"""
Article related model API
"""

import collections
import copy
import json

from .. import app
from .. import utils
from .. import remote


ARTICLE_FILENAME = 'article.md'
ARTICLE_METADATA_FILENAME = 'details.json'

path_details = collections.namedtuple('path_details', 'repo, filename')


def main_article_path():
    """Get path to main repo"""

    return '%s/%s' % (app.config['REPO_OWNER'], app.config['REPO_NAME'])


def get_available_articles():
    """
    Get iterator for current article objects

    :returns: Iterator through article objects

    Note that article objects only have path, title and author name filled out.
    You'll need to call read_article() to get full article details.
    """

    # Go through the minimal listing of articles and turn it into the full
    # article objects.  This way the github layer only knows what's available
    # on github and doesn't have knowledge of how we organize things, etc.
    for file_details in remote.files_from_github(main_article_path(),
                                                 ARTICLE_FILENAME):

        path_info = parse_full_path(file_details.path)
        json_str = read_meta_data_for_article_path(file_details.path)

        # No meta data file available
        if json_str is None:
            # FIXME: Cannot do anything here b/c we do not know the title.
            # FIXME: Handle this by logging at least
            continue
        else:
            article = Article.from_json(json_str)
            article.filename = path_info.filename
            article.repo_path = path_info.repo

        yield article


def read_article(path, rendered_text=True, branch='master'):
    """
    Read article

    :params path: Short path to article, not including repo or owner
    :params branch: Name of branch to read file from
    :returns: Article object
    """

    full_path = '%s/%s' % (main_article_path(), path)
    text, sha, github_url = remote.read_file_from_github(full_path,
                                                         branch,
                                                         rendered_text)
    if None in (text, sha):
        # FIXME: Handle error here
        return None

    # Parse path to get article information but replace it with improved json
    # meta-data if available.
    path_info = parse_full_path(full_path)
    json_str = read_meta_data_for_article_path(full_path, branch=branch)

    if json_str is not None:
        article = Article.from_json(json_str)

        # Update it with what we pull from the article file and path
        article.content = text
        article.sha = sha
        article.external_url = github_url
        article.filename = path_info.filename
        article.repo_path = path_info.repo
        article.branch = branch
    else:
        # FIXME: Log something here? We cannot properly show an article without
        # metadata
        article = None

    return article


def save_article(title, path, message, new_content, author_name, email, sha,
                 branch='master'):
    """
    Create or save new (original) article, not branched article

    :params title: Title of article
    :params path: Short path to article, not including repo or owner, or empty
                  for a new article
    :params message: Commit message to save article with
    :params content: Content of article
    :params author_name: Name of author who wrote article
    :params email: Email address of author
    :params sha: Optional SHA of article if it already exists on github
    :params branch: Name of branch to commit file to (branch must already
                    exist)

    :returns: Article object updated or saved

    This function is not suitable for saving branched articles.  The article
    created here will be attributed to the given author_name whereas branched
    articles should be created with branch_article() so the correct author
    information is maintained.
    """

    article = Article(title, author_name, branch=branch)
    if path:
        article.path = path

    status = remote.commit_file_to_github(article.full_path, message,
                                          new_content, author_name, email, sha,
                                          branch)
    if status not in (201, 200):
        # FIXME: Handle error
        return None

    if branch != 'master':
        status = save_branched_article_meta_data(article, author_name, email)
    else:
        status = save_article_meta_data(article, author_name, email, branch)

    if status not in (201, 200):
        # FIXME: Handle error. This is interesting b/c now we created the
        # article, but not the meta data.
        return None

    return read_article(article.path, rendered_text=True, branch=article.branch)


def branch_article(article, message, new_content, author_name, email):
    """
    Create branch for article with new article contents

    :params article: Article object to branch
    :params message: Message describing article suggestions/changes
    :params new_content: New article text
    :params author_name: Name of author for article changes
    :params email: Email of author for article changes
    :returns: New article object

    New branch will be named after original author
    """

    branch = author_name

    # Create branch if we needed to
    repo_sha, status = remote.read_branch(article.repo_path, branch)
    if status == 404:
        repo_sha, status = remote.read_branch(article.repo_path, 'master')
        assert repo_sha is not None, 'Cannot find master branch'

        if not remote.create_branch(article.repo_path, branch, repo_sha):
            # FIXME: Handle error
            return None

    return save_article(article.title, article.path, message, new_content,
                        author_name, email, article.sha, branch=branch)


def branch_or_save_article(title, path, message, content, author_name, email,
                           sha):
    """
    Save article as original or as a branch depending on if given author is
    the same as original article (if it already exists)

    :params title: Title of article
    :params path: Short path to article, not including repo or owner, or empty
                  for a new article
    :params message: Commit message to save article with
    :params content: Content of article
    :params author_name: Name of author who wrote content
    :params email: Email address of author
    :params sha: Optional SHA of article if it already exists on github
    :params branch: Name of branch to commit file to (branch must already
                    exist)

    :returns: Article object updated, saved, or branched
    """

    article = read_article(path, rendered_text=False, branch='master')

    if article and article.author_name != author_name and sha:
        new = branch_article(article, message, content, author_name, email)
    else:
        new = save_article(title, path, message, content, author_name, email,
                           sha)

    return new


def save_article_meta_data(article, author_name, email, branch=None):
    """
    :params article: Article object
    :params name: Name of author who wrote article
    :params email: Email address of author
    :params branch: Optional branch to save metadata, if not given
                    article.branch will be used
    :returns: HTTP status of saving meta data
    """

    filename = meta_data_path_for_article_path(article.full_path)

    if branch is None:
        branch = article.branch

    # Get sha of meta data if it exists so we can update it
    text, sha, github_url = remote.read_file_from_github(filename,
                                                         rendered_text=False,
                                                         branch=branch)

    # Don't need to serialize everything, just the important stuff that's not
    # stored in the path and article.
    exclude_attrs = ('content', 'external_url', 'sha', 'repo_path', 'path')
    json_content = article.to_json(exclude_attrs=exclude_attrs)

    message = 'Updating article metadata for %s' % (article.title)

    # Article is on a branch so we have to update the master meta data file
    # with this new branch as well as the branch meta data file.

    return remote.commit_file_to_github(filename, message, json_content,
                                        author_name, email, sha,
                                        branch=branch)


def read_meta_data_for_article_path(full_path, branch='master'):
    """
    Read meta data for given article path

    :params full_path: Full path to article
    :params branch: Name of branch to read file from
    :returns: Meta-data for article as json
    """

    filename = meta_data_path_for_article_path(full_path)
    text, sha, github_url = remote.read_file_from_github(filename,
                                                         rendered_text=False,
                                                         branch=branch)
    return text


def meta_data_path_for_article_path(full_path):
    """
    Get path to meta data file for given article path

    :params full_path: Article object
    :returns: Full path to meta data file for article
    """

    # Last part is the filename, which we're replacing. The meta data file will
    # be stored right next to the article.
    meta_data_path = '/'.join(full_path.split('/')[:-1])

    return '%s/%s' % (meta_data_path, ARTICLE_METADATA_FILENAME)


def save_branched_article_meta_data(article, author_name, email):
    """
    Save metadata for branched article

    :params article: Article object with branch attribute set to branch name
    :params name: Name of author who wrote branched article
    :params email: Email address of branched article author
    :returns: HTTP status code of saving metadata

    Metadata for branched articles should be identical to the original article.
    This makes it easier for automatically merging changes because metadata
    differences won't get in the way.  The author_name is the only thing useful
    for a branched article.  However, that should already be encoded in the
    branch name and the commits.  So, editors of original articles will get
    credit for helping via those mechanisms, not metadata.
    """

    orig_article = read_article(article.path, rendered_text=False,
                                branch='master')

    # Nothing to save, we're already tracking this branch
    if article.branch in orig_article.branches:
        return 200

    orig_article.branches.append(article.branch)

    # Note we don't ever change metadata on the branches. This keeps the
    # metadata from showing in up in merges. We only want to deal with article
    # text for merges.
    return save_article_meta_data(orig_article, author_name, email)


def parse_full_path(path):
    """
    Parse full path and return tuple of details embedded in path

    :params path: Full path to file including repo and owner
    :returns: path_details tuple
    """

    tokens = path.split('/')

    # Repo path is user/repo_name
    repo_path = '/'.join(tokens[:2])
    filename = tokens[-1]

    return path_details(repo_path, filename)


class Article(object):
    def __init__(self, title, author_name, filename=ARTICLE_FILENAME,
                 repo_path=None, branch='master', language=None, sha=None,
                 content=None, external_url=None):
        self.title = title
        self.author_name = author_name
        self.language = language
        self.content = content
        self.external_url = external_url
        self.filename = filename

        # Only useful if article has already been saved to github
        self.sha = sha

        self.repo_path = repo_path
        if self.repo_path is None:
            self.repo_path = main_article_path()

        # Branch this article is on
        self.branch = branch

        # List of branch names where this article also exists
        self.branches = []

        self.path = '%s/%s' % (utils.slugify(self.title), self.filename)

        if self.language is not None:
            self.path = '%s/%s' % (self.language, self.path)

    def __repr__(self):
        return '<author_name: %s title: %s>' % (self.author_name, self.title)

    @staticmethod
    def from_json(str_):
        """
        Create article object from json string

        :param str_: json string representing article
        :returns: Article object
        """

        dict_ = json.loads(str_)

        # Required arguments
        title = dict_.pop('title', None)
        author_name = dict_.pop('author_name', None)

        article = Article(title, author_name)
        for attr, value in dict_.iteritems():
            setattr(article, attr, value)

        return article

    def to_json(self, exclude_attrs=None):
        """
        Return json representation of article

        :param exclude_attrs: List of attributes to exclude from serialization
        :returns: json representation of article as a string
        """

        if exclude_attrs is None:
            exclude_attrs = []

        # This is very simple by design. We don't have a lot of crazy nested
        # data here so just do the bare minimum without over-engineering.
        dict_ = copy.deepcopy(self.__dict__)
        for attr in exclude_attrs:
            del dict_[attr]

        # Print it to a string in a pretty format. Whitespace doesn't matter so
        # might as well make it more readable.
        return json.dumps(dict_, sort_keys=True, indent=4,
                          separators=(',', ': '))

    @property
    def full_path(self):
        return '%s/%s' % (self.repo_path, self.path)