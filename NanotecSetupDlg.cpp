// NanotecSetupDlg.cpp : implementation file
//

#include "StdAfx.h"
#include "Resource.h"
#include "NanotecSetupDlg.h"
#include "RS232SetupDlg.h"


#ifdef _DEBUG
#define new DEBUG_NEW
#undef THIS_FILE
static char THIS_FILE[] = __FILE__;
#endif

/////////////////////////////////////////////////////////////////////////////
// CNanotecSetupDlg property page

IMPLEMENT_DYNCREATE(CNanotecSetupDlg, CPropertyPage)

CNanotecSetupDlg::CNanotecSetupDlg() : CPropertyPage(CNanotecSetupDlg::IDD)
{
	//{{AFX_DATA_INIT(CNanotecSetupDlg)
	bRS485 = FALSE;
	dwRS232TimeOutFactor = 7;
	sComPort = "Com1";
	bMonitor = FALSE;
	dwMaxNoOfRetries = 1;

	bMotor1 = bMotor2 = bInverted1 = bInverted2 = FALSE;
	dwAdr1 = 1;
	dwAdr2 = 2;
	dwMaxSteps1 = 1000;
	dwMaxSteps2 = 3000;
	//}}AFX_DATA_INIT
}

CNanotecSetupDlg::~CNanotecSetupDlg()
{
}

void CNanotecSetupDlg::DoDataExchange(CDataExchange* pDX)
{
	CPropertyPage::DoDataExchange(pDX);
	//{{AFX_DATA_MAP(CNanotecSetupDlg)
	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_RS485, bRS485 );
	DDX_CBString( pDX, IDC_NANOTEC_SETUP_DLG_RS232_COMPORT, sComPort );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_RS232_TIMEOUTFACTOR, dwRS232TimeOutFactor );
	DDV_MinMaxInt( pDX, dwRS232TimeOutFactor, 1, 100 );

	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_RS485_MONITORING, bMonitor );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_RS485_RETRIES, dwMaxNoOfRetries );
	DDV_MinMaxInt( pDX, dwMaxNoOfRetries, 0, 10 );

	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_MOTOR1, bMotor1 );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_ADR1, dwAdr1 );
	DDV_MinMaxInt( pDX, dwAdr1, 1, 255 );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_MAX_STEPS1, dwMaxSteps1 );
	DDV_MinMaxInt( pDX, dwMaxSteps1, 10, 1000000 );
	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_MOTOR1_INVERTED, bInverted1 );
	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_MOTOR2, bMotor2 );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_ADR2, dwAdr2 );
	DDV_MinMaxInt( pDX, dwAdr2, 1, 255 );
	DDX_Text( pDX, IDC_NANOTEC_SETUP_DLG_MAX_STEPS2, dwMaxSteps2 );
	DDV_MinMaxInt( pDX, dwMaxSteps2, 10, 1000000 );
	DDX_Check( pDX, IDC_NANOTEC_SETUP_DLG_MOTOR2_INVERTED, bInverted2 );
	//}}AFX_DATA_MAP
}

BEGIN_MESSAGE_MAP(CNanotecSetupDlg, CPropertyPage)
	//{{AFX_MSG_MAP(CNanotecSetupDlg)
	ON_BN_CLICKED(IDC_NANOTEC_SETUP_DLG_RS485, OnRS485SetupDlgRS232)
	ON_BN_CLICKED(IDC_NANOTEC_SETUP_DLG_RS232_SETUP_COMPORT, OnRS485SetupDlgSetupComPort)
	ON_BN_CLICKED(IDC_NANOTEC_SETUP_DLG_RS485_MONITORING, OnRS485SetupDlgMonitoring)

	ON_BN_CLICKED( IDC_NANOTEC_SETUP_DLG_MOTOR1, OnMotor1 )
	ON_BN_CLICKED( IDC_NANOTEC_SETUP_DLG_MOTOR2, OnMotor2 )
	//}}AFX_MSG_MAP
END_MESSAGE_MAP()

/////////////////////////////////////////////////////////////////////////////
// CNanotecSetupDlg message handlers

BOOL CNanotecSetupDlg::OnSetActive() 
{
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_TIMEOUTFACTOR )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_COMPORT )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_SETUP_COMPORT )->EnableWindow( bRS485 );

	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS485_MONITORING )->EnableWindow( bRS485 );
	if ( !bRS485 )
		bMonitor = bMotor1 = bMotor2 = FALSE;
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS485_RETRIES )->EnableWindow( bMonitor );

	sComPort = SetupCom.pComPort->c_str();
	int j = atoi( sComPort.Right(1) );
	CComboBox *box = (CComboBox *) GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_COMPORT );
	box->SetCurSel( j-1 );

	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR1 )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_ADR1 )->EnableWindow( bMotor1 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MAX_STEPS1 )->EnableWindow( bMotor1 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR1_INVERTED )->EnableWindow( bMotor1 );

	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR2 )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_ADR2 )->EnableWindow( bMotor2 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MAX_STEPS2 )->EnableWindow( bMotor2 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR2_INVERTED )->EnableWindow( bMotor2 );

//	UpdateData( FALSE );
	return CPropertyPage::OnSetActive();
}

void CNanotecSetupDlg::OnRS485SetupDlgRS232() 
{
	UpdateData( TRUE );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_TIMEOUTFACTOR )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_COMPORT )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS232_SETUP_COMPORT )->EnableWindow( bRS485 );

	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS485_MONITORING )->EnableWindow( bRS485 );
	if ( !bRS485 )
		bMonitor = bMotor1 = bMotor2 = FALSE;
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS485_RETRIES )->EnableWindow( bMonitor );

	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR1 )->EnableWindow( bRS485 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR2 )->EnableWindow( bRS485 );
	UpdateData( FALSE );
}

void CNanotecSetupDlg::OnRS485SetupDlgSetupComPort()
{
	UpdateData( TRUE );
	
	CRS232SetupDlg RS232SetupDlg;
	RS232SetupDlg.dwBaudRate = SetupCom.dwBaudRate; 
	RS232SetupDlg.dwByteSize = SetupCom.dwByteSize; 
	RS232SetupDlg.dwStopBits = SetupCom.dwStopBits; 
	RS232SetupDlg.sParity = SetupCom.pParity->c_str(); 

	if ( RS232SetupDlg.DoModal() == IDOK )
	{
		SetupCom.dwBaudRate = RS232SetupDlg.dwBaudRate; 
		SetupCom.dwByteSize = RS232SetupDlg.dwByteSize; 
		SetupCom.dwStopBits = RS232SetupDlg.dwStopBits; 
		*SetupCom.pParity = RS232SetupDlg.sParity; 		
	}
}

void CNanotecSetupDlg::OnRS485SetupDlgMonitoring() 
{
	UpdateData( TRUE );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_RS485_RETRIES )->EnableWindow( bMonitor );
}

void CNanotecSetupDlg::OnMotor1() 
{
	UpdateData( TRUE );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_ADR1 )->EnableWindow( bMotor1 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MAX_STEPS1 )->EnableWindow( bMotor1 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR1_INVERTED )->EnableWindow( bMotor1 );
}

void CNanotecSetupDlg::OnMotor2() 
{
	UpdateData( TRUE );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_ADR2 )->EnableWindow( bMotor2 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MAX_STEPS2 )->EnableWindow( bMotor2 );
	GetDlgItem( IDC_NANOTEC_SETUP_DLG_MOTOR2_INVERTED )->EnableWindow( bMotor2 );
}

void CNanotecSetupDlg::OnOK()
{
	UpdateData( TRUE );
	*SetupCom.pComPort = sComPort;
	CPropertyPage::OnOK();
}
