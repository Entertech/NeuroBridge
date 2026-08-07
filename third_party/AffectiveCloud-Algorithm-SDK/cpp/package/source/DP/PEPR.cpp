//
// Created by Enter M1 on 2023/9/4.
//

#include "PEPR.h"
#include "Device.h"
#include "PEPRHandler.h"
#include "Basic.hpp"
using namespace basic;
using namespace dsp;
namespace dp
{
    void PEPRProgress::tempInit()
    {
        temp.index = 0;
        basic::dsp::peprhandler::initTemp(temp.peprHandlerTmp);
        temp.hrRec.clear();
        temp.hrvRec.clear();
        temp.rrRec.clear();
        temp.bcgQualityRec.clear();
        temp.rwQualityRec.clear();
    }

    PEPRProgress::PEPRProgress()
    {
        tempInit();
    }

    PEPRProgress::~PEPRProgress()
    {

    }

    PEPRTriggerRes PEPRProgress::trigger(basic::SessionCache &cache,const vectori &peData, const vectori &prData)
    {
        //初始化
        auto *deviceInfo = new DeviceInfoCushion();

        //算法执行
        auto peprRes = peprhandler::handler(
            peData,
            prData,
            7.8, 30, 5., 25., 1., 2.,
            deviceInfo,
            temp.peprHandlerTmp
        );

        //更新内部缓存
        temp.index += 1;
        temp.hrRec.push_back((int)peprRes.hr);
        temp.hrvRec.push_back(peprRes.hrv);
        temp.rrRec.push_back(peprRes.rr);
        temp.bcgQualityRec.push_back(static_cast<int>(peprRes.bcgQuality));
        temp.rwQualityRec.push_back(static_cast<int>(peprRes.rwQuality));
        //更新外部缓存
        cache.hr = (int)peprRes.hr;
        cache.hrv = peprRes.hrv;
        cache.hrSyncCor = peprRes.syncCor;
        cache.bcgQuality = peprRes.bcgQuality;
        cache.rwQuality = peprRes.rwQuality;
        cache.hrPower = peprRes.hrPower;

        //兼容V1版本（用于V1版本情感计算）
        HRQuality hrQuality = HRQuality::INVALID;
        if (peprRes.bcgQuality ==BCGQuality::BCG_NORM)
            hrQuality = HRQuality::VALID;
        cache.hrQuality = hrQuality;


        delete deviceInfo;
        PEPRTriggerRes res;
        res.hr = static_cast<int>(peprRes.hr);
        res.hrv = peprRes.hrv;
        res.bcgQuality = peprRes.bcgQuality;
        res.rwQuality = peprRes.rwQuality;
        res.bcgWave = peprRes.bcgWave;
        res.rwWave = peprRes.rwWave;
        res.rr = peprRes.rr;
        return res;
    }

    PEPRReportRes PEPRProgress::report() const
    {
        PEPRReportRes res;
        if (temp.hrRec.empty())
            return res;
        res.hrRec = temp.hrRec;
        res.rrRec = temp.rrRec;
        res.hrvRec = temp.hrvRec;
        vectori hrRecTmp;
        for (auto e: temp.hrRec) {
            if (e > 0)
                hrRecTmp.push_back(e);
        }
        vectord hrvRecTmp;
        for (auto e: temp.hrvRec) {
            if (e > 0)
                hrvRecTmp.push_back(e);
        }
        vectord rrRecTmp;
        for (auto e: temp.rrRec) {
            if (e > 0)
                rrRecTmp.push_back((double)e);
        }
        if (hrRecTmp.empty())
        {
            res.hrAvg = 0;
            res.hrMin = 0;
            res.hrMax = 0;
        }
        else
        {
            res.hrAvg = mathtool::mean(hrRecTmp);
            res.hrMin = mathtool::min(hrRecTmp);
            res.hrMax = mathtool::max(hrRecTmp);
        }
        if (rrRecTmp.empty())
        {
            res.rrAvg = 0;
        }
        else
        {
            res.rrAvg = (int)ceil(mathtool::mean(rrRecTmp));
        }
        if (hrvRecTmp.empty())
        {
            res.hrvAvg = 0.;
        }
        else
        {
            res.hrvAvg = mathtool::mean(hrvRecTmp);
        }

        res.bcgQualityRec = temp.bcgQualityRec;
        res.rwQualityRec = temp.rwQualityRec;
        return res;
    }


}
