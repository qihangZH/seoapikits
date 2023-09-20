import typing
import pandas as pd
import time
import tqdm
from . import _basic


class SERPAPI:

    def __init__(self,
                 username,
                 password,
                 search_engine='google',
                 search_type='organic',
                 search_method: typing.Literal['advanced', 'regular'] = 'regular',
                 language_code='en',
                 location_code: int = 2840
                 ):
        """
        :param username:
        :param password:
        :param search_engine: the search engine using, like google(default)
        :param search_type: the search type of special engine, like organic(default)
        :param search_method: the search method of SERP, like regular(default)
        :param language_code:
        :param location_code:
        """

        self.username = username
        self.password = password
        self.search_engine = search_engine
        self.search_type = search_type
        self.search_method = search_method
        self.language_code = language_code
        self.location_code = location_code

        # start the client
        self._start_client()

    def _start_client(self):
        self.client = _basic.RestClient(username=self.username, password=self.password)

    def tasks_seq_post(self,
                       keyword_list,
                       res_type: typing.Literal['df', 'raw'] = 'df',
                       restrict_chunksize_per_post=100
                       ):
        """
        More details see: https://docs.dataforseo.com/v3/serp/
        :param keyword_list: the keywords list return
        :param res_type: the result type, normally be 'raw'/'df', raw mean raw json,
            while df means deduce dataframe, dataframe columns be ['id','keyword']
        :param restrict_chunksize_per_post: see their website of per-time-post detail,
            default is 100 and SHOULD NOT CHANGE!
        :return: data-frame/ a list of json dict, each contains info length of restrict_chunksize_per_post
        """
        if not (res_type in ['df', 'raw']):
            raise ValueError('res_type must be df/raw, no other choices')

        input_keywords = []
        for keyword in keyword_list:
            request_dic = {
                "language_code": self.language_code,
                "location_code": self.location_code,
                "keyword": keyword
            }
            input_keywords.append(request_dic)

        result_list = []

        for kwchunks in [input_keywords[s:s + restrict_chunksize_per_post]
                         for s in range(0, len(input_keywords), restrict_chunksize_per_post)]:

            post_res = self.client.post(
                f"/v3/serp/{self.search_engine}/{self.search_type}/task_post",
                kwchunks
            )
            if post_res["status_code"] == 20000:

                if res_type == 'df':
                    res_df = pd.json_normalize(
                        data=post_res['tasks'],
                        max_level=1
                    )[['id', 'data.keyword']].rename(columns={'data.keyword': 'keyword'})

                    if set(res_df['keyword'].to_list()) == set([tmpd['keyword'] for tmpd in kwchunks]):
                        result_list.append(res_df)

                    else:
                        raise ValueError('Error occurs in Dataframe for wrong keyword response')

                elif res_type == 'raw':
                    result_list.append(post_res)
            else:
                raise ValueError("error. Code: %d Message: %s" % (post_res["status_code"], post_res["status_message"]))

        """finally we have to concat the result"""
        if res_type == 'df':
            return pd.concat(result_list, axis=0).reset_index(drop=True)
        else:
            return result_list

    def tasks_seq_ready_df(self,
                           task_id_listarrser
                           ):
        """check if the task are already finished"""
        # using this method you can get a list of completed tasks
        # GET /v3/serp/google/organic/tasks_ready
        # in addition to 'google' and 'organic' you can also set other search engine and type parameters
        # the full list of possible parameters is available in documentation
        # you can find the full list of the response codes here https://docs.dataforseo.com/v3/appendix/errors

        get_res = self.client.get(f"/v3/serp/{self.search_engine}/{self.search_type}/tasks_ready")

        ready_ids_list = pd.DataFrame(get_res["tasks"][0]["result"]).copy()['id'].to_list()

        task_id_list = pd.Series(task_id_listarrser).to_list()

        return pd.DataFrame(
            {
                'id': task_id_list,
                'ready': [tid in ready_ids_list for tid in task_id_list]
            }
        )

    def task_get(self,
                 task_id,
                 res_type: typing.Literal['df', 'raw'] = 'df',
                 timeout_limit=0,
                 retry_freq=5
                 ):
        """
        :param task_id: the task id
        :param res_type: the result type, normally be 'raw'/'df',
            raw mean raw json, while df means dataframe
        :param timeout_limit: default 0(seconds), the time limit to retry for request the data from server
        :param retry_freq: the frequency(seconds) to try again from seo server
        :return: data-frame/ json dict
        """

        def _lambda_get_res():
            return self.client.get(
                f"/v3/serp/{self.search_engine}/{self.search_type}/"
                f"task_get/{self.search_method}/" +
                # "/v3/serp/google/organic/task_get/regular/" +
                task_id
            )

        if not (res_type in ['df', 'raw']):
            raise ValueError('res_type must be df/raw, no other choices')

        get_res = _lambda_get_res()

        # make a special part to make sure we can got not None result
        timeout_start = time.time()
        while True:
            if (not get_res["tasks"][0]["result"]) or (get_res['status_code'] != 20000):

                if (time.time() - timeout_start) >= timeout_limit:
                    # if time out then have a value error cast
                    raise ValueError('Error-timeout for not get a efficient response from Seodata')
                else:
                    # if time not out then retry..., sleep for seconds
                    time.sleep(retry_freq)
                    get_res = _lambda_get_res()
                    continue
            else:
                break

        if res_type == 'df':

            res_df = pd.DataFrame(get_res["tasks"][0]["result"][0]["items"]).copy()

            res_df['id'] = task_id

            return res_df
        else:
            return get_res

    def tasks_seq_get_df(self, task_id_listarrser, timeout_limit=0, retry_freq=5,
                         errors: typing.Literal['strict', 'ignore'] = 'strict'):
        """
        :param task_id_listarrser: the task id list, If meets the problems of No-return, then auto-rerun
        :param timeout_limit: default 0(seconds), the time limit to retry for request the data from server
        :param retry_freq: the frequency(seconds) to try again from seo server
        :param errors: the actions to deal with errors, default 'strict', 'ignore' means pass and leave NaN
        :return: data-frame ONLY
        """
        res_df_list = []

        for taskid in tqdm.tqdm(task_id_listarrser):

            try:
                tmp_df = self.task_get(
                    task_id=taskid,
                    res_type='df',
                    timeout_limit=timeout_limit,
                    retry_freq=retry_freq
                )
            except Exception as e:
                if errors == 'strict':
                    raise ConnectionError(e)
                elif errors == 'ignore':
                    print(e)
                    print('\nthe problem was ignored and pass to next id get')
                    tmp_df = pd.DataFrame({'id': [taskid]})
                else:
                    raise ValueError('errors must in argument choices.')

            res_df_list.append(
                tmp_df
            )

        return pd.concat(res_df_list, axis=0).reset_index(drop=True)
