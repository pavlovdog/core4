import Vue from 'vue'
import Vuex from 'vuex'
import createLogger from 'vuex/dist/logger'

import { clone } from 'pnbi-base/core4/helper'
import { createObjectWithDefaultValues } from './helper'

import { jobStates, jobGroups } from './settings.js'

Vue.use(Vuex)

const colors = {
  pending: '#ffc107',
  deferred: '#f1f128',
  failed: '#11dea2',
  running: '#64a505',
  error: '#d70f14',
  inactive: '#8d1407',
  killed: '#d8c9c7'
}

var chartInitValue = {
  timestemp: '',
  pending: 0,
  deferred: 0,
  failed: 0,
  running: 0,
  error: 0,
  inactive: 0,
  killed: 0
}

export default new Vuex.Store({
  plugins: [createLogger()],
  state: {
    queue: {},
    chartValues: {},
    socket: {
      isConnected: false,
      message: '',
      reconnectError: false
    }
  },
  actions: {},
  mutations: {
    SOCKET_ONOPEN (state, event) {
      Vue.prototype.$socket = event.currentTarget
      Vue.prototype.$socket.sendObj({ 'type': 'interest', 'data': ['queue'] })
      state.socket.isConnected = true
    },
    SOCKET_ONCLOSE (state, event) {
      state.socket.isConnected = false
    },
    SOCKET_ONERROR (state, event) {
      // ToDo: add error flow (message, pop-up etc)
      console.error(state, event)
    },
    // default handler called for all methods
    SOCKET_ONMESSAGE (state, message) {
      state.socket.message = message

      // summary - ws type notification (all jobs in queue)
      if (message.name === 'summary') {
        state.queue = groupDataAndJobStat(message.data, 'state')

        let obj = {
          pending: 0,
          deferred: 0,
          failed: 0,
          running: 0,
          error: 0,
          inactive: 0,
          killed: 0
        }

        for (let key in message.data.queue) {
          obj[key] = message.data.queue[key]
        }

        state.chartValues = obj
      }

      if (message.name === 'enqueue_job') {
        let obj = {
          pending: 0,
          deferred: 0,
          failed: 0,
          running: 0,
          error: 0,
          inactive: 0,
          killed: 0
        }

        for (let key in message.data.queue) {
          obj[key] = message.data.queue[key]
        }

        state.chartValues = obj
      }
    },
    // mutations for reconnect methods
    SOCKET_RECONNECT (state, count) {
      console.info(state, count)
    },
    SOCKET_RECONNECT_ERROR (state) {
      // ToDo: add error flow (message, pop-up etc)
      state.socket.reconnectError = true
    }
  },
  getters: {
    ...mapGettersJobGroups(jobGroups),
    getChart: (state) => {
      return state.chartValues
    },
    getJobsByGroupName: (state, getters) => (groupName) => {
      return getters[groupName]
    },
    getStateCounter: (state) => (stateName) => {
      if (state.queue.stat === undefined) return 0

      return stateName.reduce((previousValue, currentItem) => {
        previousValue += state.queue.stat[currentItem] || 0

        return previousValue
      }, 0)
    }
  }
})

// ================================================================= //
// Private methods
// ================================================================= //

/**
 * Getter(s) for job(s) group from store
 *
 * @param {array} arr -  group(s)
 *                       e.g. ['waiting', 'running', 'stopped']
 *
 * @returns {object} - object with key - group name, value - getter function
 *                     e.g. {'running': (state) => f, ...}
 */
function mapGettersJobGroups (arr) {
  return arr.reduce((computedResult, currentItem) => {
    computedResult[currentItem] = (state) => {
      return clone(state.queue[currentItem] || [])
    }

    return computedResult
  }, {})
}

/**
 * Assort array of all jobs in groups + get job statistic
 *
 * @param {array} arr - array of all jobs
 * @param {string} groupingKey - job object key by which we will do grouping
 *
 * @returns {object} - grouped jobs object
 *                     e. g. {'stat': {'waiting': 5, ...}, 'running': [<job>, ..., <job>], ...}
 */
// ToDo: elegant decouple group data and job statistic
function groupDataAndJobStat (arr, groupingKey) {
  let groupsDict = {}
  let initialState = createObjectWithDefaultValues(jobStates)

  arr.forEach((job) => {
    let jobState = job[groupingKey]
    let group = jobStates[jobState] || 'other';

    (groupsDict[group] = groupsDict[group] || []).push(job)

    initialState[jobState] += job['n']
  })

  return { 'stat': initialState, ...groupsDict }
}
